"""Small, versioned SQLite migrations for MathBank.

The project intentionally avoids a heavyweight migration framework.  This
module keeps schema upgrades explicit, backup-first, and fail-closed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from mathbank.paths import SCHEMA_SNAPSHOT_DIR
from mathbank.sub_knowledge import normalize_sub_knowledge


LATEST_SCHEMA_VERSION = 13
LEGACY_REQUIRED_TABLES = {
    "questions",
    "question_curriculums",
    "papers",
    "paper_questions",
}
REQUIRED_TABLES = LEGACY_REQUIRED_TABLES | {
    "question_fingerprints",
    "question_tags",
    "question_relations",
    "question_sub_knowledge",
    "topics",
    "topic_groups",
    "topic_items",
    "topic_versions",
    "topic_links",
    "question_usage_stats",
}

#: 专题工坊六表（v13）。刻意不纳入 SELF_HEALING_TABLES：这些表存用户原创
#: 教学设计（等同 papers），不是可从其他表派生的索引；缺表时重建空表等于
#: 静默数据丢失，必须 fail-closed 引导用户从 data_backup 恢复备份。
TOPIC_TABLES = (
    "topics",
    "topic_groups",
    "topic_items",
    "topic_versions",
    "topic_links",
    "question_usage_stats",
)

V6_QUESTION_FINGERPRINT_COLUMNS = {
    "question_id",
    "fingerprint_version",
    "content_revision_hash",
    "exact_hash",
    "critical_math_hash",
    "answer_hash",
    "simhash_hex",
    "token_count",
    "choice_count",
    "figure_count",
    "visible_image_hashes",
    "tikz_hashes",
    "band0",
    "band1",
    "band2",
    "band3",
    "band4",
    "band5",
    "band6",
    "band7",
    "status",
    "updated_at",
}
V7_QUESTION_FINGERPRINT_COLUMNS = set(V6_QUESTION_FINGERPRINT_COLUMNS)
QUESTION_FINGERPRINT_COLUMNS = V7_QUESTION_FINGERPRINT_COLUMNS | {
    "text_band0",
    "text_band1",
    "text_band2",
    "text_band3",
    "text_band4",
    "text_band5",
    "text_band6",
    "text_band7",
}

V6_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": ("fingerprint_version", "band0"),
    "idx_question_fingerprints_band1": ("fingerprint_version", "band1"),
    "idx_question_fingerprints_band2": ("fingerprint_version", "band2"),
    "idx_question_fingerprints_band3": ("fingerprint_version", "band3"),
    "idx_question_fingerprints_band4": ("fingerprint_version", "band4"),
    "idx_question_fingerprints_band5": ("fingerprint_version", "band5"),
    "idx_question_fingerprints_band6": ("fingerprint_version", "band6"),
    "idx_question_fingerprints_band7": ("fingerprint_version", "band7"),
}

V7_QUESTION_FINGERPRINT_INDEXES = {
    "idx_question_fingerprints_exact": ("fingerprint_version", "exact_hash"),
    "idx_question_fingerprints_band0": (
        "fingerprint_version",
        "band0",
        "token_count",
    ),
    "idx_question_fingerprints_band1": (
        "fingerprint_version",
        "band1",
        "token_count",
    ),
    "idx_question_fingerprints_band2": (
        "fingerprint_version",
        "band2",
        "token_count",
    ),
    "idx_question_fingerprints_band3": (
        "fingerprint_version",
        "band3",
        "token_count",
    ),
    "idx_question_fingerprints_band4": (
        "fingerprint_version",
        "band4",
        "token_count",
    ),
    "idx_question_fingerprints_band5": (
        "fingerprint_version",
        "band5",
        "token_count",
    ),
    "idx_question_fingerprints_band6": (
        "fingerprint_version",
        "band6",
        "token_count",
    ),
    "idx_question_fingerprints_band7": (
        "fingerprint_version",
        "band7",
        "token_count",
    ),
}

QUESTION_FINGERPRINT_INDEXES = {
    **V7_QUESTION_FINGERPRINT_INDEXES,
    "idx_question_fingerprints_text_band0": (
        "fingerprint_version",
        "text_band0",
        "token_count",
    ),
    "idx_question_fingerprints_text_band1": (
        "fingerprint_version",
        "text_band1",
        "token_count",
    ),
    "idx_question_fingerprints_text_band2": (
        "fingerprint_version",
        "text_band2",
        "token_count",
    ),
    "idx_question_fingerprints_text_band3": (
        "fingerprint_version",
        "text_band3",
        "token_count",
    ),
    "idx_question_fingerprints_text_band4": (
        "fingerprint_version",
        "text_band4",
        "token_count",
    ),
    "idx_question_fingerprints_text_band5": (
        "fingerprint_version",
        "text_band5",
        "token_count",
    ),
    "idx_question_fingerprints_text_band6": (
        "fingerprint_version",
        "text_band6",
        "token_count",
    ),
    "idx_question_fingerprints_text_band7": (
        "fingerprint_version",
        "text_band7",
        "token_count",
    ),
}


def _database_path(engine: Engine) -> Path | None:
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    return Path(database).resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def schema_version(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())


def create_pre_migration_backup(
    engine: Engine,
    *,
    from_version: int,
    to_version: int,
) -> Path | None:
    """Create and verify a consistent SQLite snapshot before a migration."""

    database_path = _database_path(engine)
    if database_path is None or not database_path.exists():
        return None

    backup_dir = SCHEMA_SNAPSHOT_DIR
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = backup_dir / (
        f"{database_path.stem}.schema-v{from_version}-to-v{to_version}.{timestamp}.db"
    )

    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
        journal_mode = str(target.execute("PRAGMA journal_mode=DELETE").fetchone()[0])
        if journal_mode.lower() != "delete":
            raise RuntimeError(
                f"迁移前快照无法转换为独立日志模式: {journal_mode}"
            )
        integrity = target.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"迁移前数据库快照校验失败: {integrity}")

    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{backup_path}{suffix}")
        if suffix == "-wal" and sidecar.exists() and sidecar.stat().st_size:
            raise RuntimeError(f"迁移前快照仍依赖未归档 WAL: {sidecar}")
        sidecar.unlink(missing_ok=True)

    backup_path.chmod(0o600)
    checksum_path = backup_path.with_suffix(backup_path.suffix + ".sha256")
    checksum_path.write_text(f"{_sha256(backup_path)}  {backup_path.name}\n", encoding="utf-8")
    checksum_path.chmod(0o600)
    return backup_path


def _ensure_tikz_asset_columns(connection) -> dict[str, int]:
    """Add editable TikZ asset columns when an older table lacks them."""

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "answer_tikz_assets": "TEXT DEFAULT '[]'",
        "tikz_reference_image_path": "TEXT DEFAULT ''",
        "content_tikz_assets": "TEXT DEFAULT '[]'",
    }
    stats: dict[str, int] = {}
    for column, definition in additions.items():
        added = column not in columns
        if added:
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {column} {definition}"
            )
        stats[f"added_{column}"] = int(added)
    return stats


def _ensure_figure_layout_columns(connection) -> dict[str, int]:
    """Add v9 group preferences and v10 per-image layouts without rewriting rows."""

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    additions = {
        "figure_size": "VARCHAR(20) NOT NULL DEFAULT 'auto'",
        "image_layouts": "TEXT NOT NULL DEFAULT '{}'",
        "figure_align_custom": "INTEGER NOT NULL DEFAULT 0",
    }
    stats: dict[str, int] = {}
    for column, definition in additions.items():
        added = column not in columns
        if added:
            connection.exec_driver_sql(
                f"ALTER TABLE questions ADD COLUMN {column} {definition}"
            )
        stats[f"added_{column}"] = int(added)
    return stats


def _validate_figure_layout_schema(connection) -> None:
    """Fail closed when the current database lacks a layout preference."""

    table_info = connection.exec_driver_sql(
        'PRAGMA table_info("questions")'
    ).fetchall()
    info_by_name = {row[1]: row for row in table_info}
    image_info = info_by_name.get("image_layouts")
    if image_info is None or int(image_info[3]) != 1:
        raise RuntimeError("数据库表 questions 缺少核心字段或结构异常: image_layouts")
    column_info = info_by_name.get("figure_size")
    if column_info is None:
        raise RuntimeError("数据库表 questions 缺少核心字段: figure_size")
    if int(column_info[3]) != 1 or str(column_info[4]).strip("'\"") != "auto":
        raise RuntimeError("questions.figure_size 结构异常")
    invalid_count = int(
        connection.exec_driver_sql(
            "SELECT COUNT(*) FROM questions "
            "WHERE figure_size NOT IN ('auto', 'small', 'medium', 'large')"
        ).scalar_one()
    )
    if invalid_count:
        raise RuntimeError("questions.figure_size 包含无效值")
    custom_info = info_by_name.get("figure_align_custom")
    if custom_info is None:
        raise RuntimeError(
            "数据库表 questions 缺少核心字段: figure_align_custom"
        )
    if int(custom_info[3]) != 1 or str(custom_info[4]).strip("'\"") != "0":
        raise RuntimeError("questions.figure_align_custom 结构异常")
    invalid_custom_count = int(
        connection.exec_driver_sql(
            "SELECT COUNT(*) FROM questions WHERE figure_align_custom NOT IN (0, 1)"
        ).scalar_one()
    )
    if invalid_custom_count:
        raise RuntimeError("questions.figure_align_custom 包含无效值")


def _ensure_sub_knowledge_columns(connection) -> dict[str, int]:
    """v11 adds an empty relation; no historical chapter labels/tags change."""

    exists = bool(connection.exec_driver_sql(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_sub_knowledge'"
    ).first())
    if not exists:
        connection.exec_driver_sql("""
            CREATE TABLE question_sub_knowledge (
                id INTEGER NOT NULL PRIMARY KEY,
                curriculum_id INTEGER NOT NULL,
                compulsory VARCHAR(100) NOT NULL,
                chapter VARCHAR(100) NOT NULL,
                knowledge VARCHAR(100) NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                CONSTRAINT uq_question_sub_knowledge_path UNIQUE (curriculum_id, compulsory, chapter, knowledge),
                CONSTRAINT ck_question_sub_knowledge_position CHECK (position >= 0),
                FOREIGN KEY(curriculum_id) REFERENCES question_curriculums(id) ON DELETE CASCADE
            )
        """)
        connection.exec_driver_sql("CREATE INDEX ix_question_sub_knowledge_curriculum_id ON question_sub_knowledge (curriculum_id)")
    _validate_sub_knowledge_schema(connection)
    return {"added_question_sub_knowledge": int(not exists)}


def _validate_sub_knowledge_schema(connection) -> None:
    table = "question_sub_knowledge"
    info = {row[1]: row for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()}
    fields = {"id", "curriculum_id", "compulsory", "chapter", "knowledge", "position"}
    if set(info) != fields or any(int(info[field][3]) != 1 for field in fields - {"id"}):
        raise RuntimeError(f"数据库表 {table} 缺少核心字段或结构异常")
    if int(info["id"][5]) != 1 or any(int(info[field][5]) != 0 for field in fields - {"id"}):
        raise RuntimeError("question_sub_knowledge 主键结构异常")
    if str(info["position"][4]).strip("'\"") != "0":
        raise RuntimeError("question_sub_knowledge 排序默认值异常")
    foreign_keys = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').fetchall()
    if not any(row[2] == "question_curriculums" and row[3] == "curriculum_id" and row[4] == "id"
               and str(row[6]).upper() == "CASCADE" for row in foreign_keys):
        raise RuntimeError("question_sub_knowledge 缺少版本镜像级联外键")
    indexes = connection.exec_driver_sql(f'PRAGMA index_list("{table}")').fetchall()
    if not any(tuple(entry[2] for entry in connection.exec_driver_sql(
        f'PRAGMA index_info("{row[1]}")'
    ).fetchall()) == ("curriculum_id",) for row in indexes):
        raise RuntimeError("question_sub_knowledge 缺少版本镜像索引")
    if not any(int(row[2]) == 1 and tuple(entry[2] for entry in connection.exec_driver_sql(
        f'PRAGMA index_info("{row[1]}")'
    ).fetchall()) == ("curriculum_id", "compulsory", "chapter", "knowledge") for row in indexes):
        raise RuntimeError("question_sub_knowledge 缺少完整路径唯一约束")
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check(question_sub_knowledge)").fetchall()
    if violations:
        raise RuntimeError("question_sub_knowledge 存在外键异常")
    for row in connection.exec_driver_sql("SELECT id, compulsory, chapter, knowledge, position FROM question_sub_knowledge"):
        try:
            normalize_sub_knowledge([dict(zip(("compulsory", "chapter", "knowledge"), row[1:4]))])
            if row[4] < 0:
                raise ValueError("position")
        except ValueError as exc:
            raise RuntimeError(f"question_sub_knowledge 包含无效值 (id={row[0]})") from exc


def _validate_question_fingerprint_schema(
    connection,
    *,
    expected_columns: set[str] | None = None,
    expected_indexes: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Fail closed when a versioned fingerprint table is structurally damaged."""

    expected_columns = (
        QUESTION_FINGERPRINT_COLUMNS
        if expected_columns is None
        else expected_columns
    )
    expected_indexes = (
        QUESTION_FINGERPRINT_INDEXES
        if expected_indexes is None
        else expected_indexes
    )

    table_names = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "question_fingerprints" not in table_names:
        raise RuntimeError(
            "数据库结构不完整，缺少必要数据表: question_fingerprints"
        )

    table_info = connection.exec_driver_sql(
        'PRAGMA table_info("question_fingerprints")'
    ).fetchall()
    columns = {row[1] for row in table_info}
    missing_columns = expected_columns - columns
    if missing_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 缺少核心字段: "
            + ", ".join(sorted(missing_columns))
        )
    unexpected_columns = columns - expected_columns
    if unexpected_columns:
        raise RuntimeError(
            "数据库表 question_fingerprints 包含与版本不符的字段: "
            + ", ".join(sorted(unexpected_columns))
        )

    primary_key = [
        row[1]
        for row in sorted(table_info, key=lambda item: item[5])
        if row[5]
    ]
    if primary_key != ["question_id", "fingerprint_version"]:
        raise RuntimeError("question_fingerprints 复合主键结构异常")

    foreign_keys = connection.exec_driver_sql(
        'PRAGMA foreign_key_list("question_fingerprints")'
    ).fetchall()
    has_question_cascade = any(
        row[2] == "questions"
        and row[3] == "question_id"
        and row[4] == "id"
        and str(row[6]).upper() == "CASCADE"
        for row in foreign_keys
    )
    if not has_question_cascade:
        raise RuntimeError("question_fingerprints 缺少 questions 级联删除外键")

    index_rows = {
        row[1]: row
        for row in connection.exec_driver_sql(
            'PRAGMA index_list("question_fingerprints")'
        ).fetchall()
    }
    missing_indexes = set(expected_indexes) - set(index_rows)
    if missing_indexes:
        raise RuntimeError(
            "question_fingerprints 缺少查重索引: "
            + ", ".join(sorted(missing_indexes))
        )
    managed_indexes = {
        name
        for name in index_rows
        if name.startswith("idx_question_fingerprints_")
    }
    unexpected_indexes = managed_indexes - set(expected_indexes)
    if unexpected_indexes:
        raise RuntimeError(
            "question_fingerprints 包含与版本不符的查重索引: "
            + ", ".join(sorted(unexpected_indexes))
        )
    for index_name, expected_columns in expected_indexes.items():
        actual_columns = tuple(
            row[2]
            for row in connection.exec_driver_sql(
                f'PRAGMA index_info("{index_name}")'
            ).fetchall()
        )
        if actual_columns != expected_columns:
            raise RuntimeError(f"question_fingerprints 索引结构异常: {index_name}")
        index_row = index_rows[index_name]
        if int(index_row[2]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为唯一: {index_name}"
            )
        if len(index_row) < 5 or int(index_row[4]) != 0:
            raise RuntimeError(
                f"question_fingerprints 查重索引不得设为部分索引: {index_name}"
            )


def _ensure_question_fingerprint_table(connection) -> int:
    """Create the rebuildable fingerprint table with current lookup indexes."""

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_fingerprints'"
        ).first()
    )
    if not table_exists:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_fingerprints (
                question_id INTEGER NOT NULL,
                fingerprint_version INTEGER NOT NULL,
                content_revision_hash VARCHAR(64) NOT NULL DEFAULT '',
                exact_hash VARCHAR(64) NOT NULL DEFAULT '',
                critical_math_hash VARCHAR(64) NOT NULL DEFAULT '',
                answer_hash VARCHAR(64) NOT NULL DEFAULT '',
                simhash_hex VARCHAR(32) NOT NULL DEFAULT '',
                token_count INTEGER NOT NULL DEFAULT 0,
                choice_count INTEGER NOT NULL DEFAULT 0,
                figure_count INTEGER NOT NULL DEFAULT 0,
                visible_image_hashes TEXT NOT NULL DEFAULT '[]',
                tikz_hashes TEXT NOT NULL DEFAULT '[]',
                text_band0 VARCHAR(16) NOT NULL DEFAULT '',
                text_band1 VARCHAR(16) NOT NULL DEFAULT '',
                text_band2 VARCHAR(16) NOT NULL DEFAULT '',
                text_band3 VARCHAR(16) NOT NULL DEFAULT '',
                text_band4 VARCHAR(16) NOT NULL DEFAULT '',
                text_band5 VARCHAR(16) NOT NULL DEFAULT '',
                text_band6 VARCHAR(16) NOT NULL DEFAULT '',
                text_band7 VARCHAR(16) NOT NULL DEFAULT '',
                band0 INTEGER NOT NULL DEFAULT 0,
                band1 INTEGER NOT NULL DEFAULT 0,
                band2 INTEGER NOT NULL DEFAULT 0,
                band3 INTEGER NOT NULL DEFAULT 0,
                band4 INTEGER NOT NULL DEFAULT 0,
                band5 INTEGER NOT NULL DEFAULT 0,
                band6 INTEGER NOT NULL DEFAULT 0,
                band7 INTEGER NOT NULL DEFAULT 0,
                status VARCHAR(20) NOT NULL DEFAULT 'pending',
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (question_id, fingerprint_version),
                CONSTRAINT fk_question_fingerprints_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        for index_name, columns in QUESTION_FINGERPRINT_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX {index_name} ON question_fingerprints "
                f"({', '.join(columns)})"
            )

    _validate_question_fingerprint_schema(connection)
    return int(not table_exists)


QUESTION_TAG_INDEXES = {
    "idx_question_tags_dim_code": ("dim", "code"),
    "idx_question_tags_question": ("question_id",),
}


def _ensure_question_tags_table(connection) -> dict[str, int]:
    """Create the multi-value tag table and seed it from legacy categories.

    Existing 册/章/节 columns stay untouched: the tag table is additive so a
    rollback only loses the derived index, never the original classification.
    """

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_tags'"
        ).first()
    )
    if not table_exists:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_tags (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                dim VARCHAR(20) NOT NULL,
                code VARCHAR(120) NOT NULL,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_question_tag UNIQUE (question_id, dim, code),
                CONSTRAINT fk_question_tags_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        for index_name, columns in QUESTION_TAG_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX {index_name} ON question_tags "
                f"({', '.join(columns)})"
            )
    else:
        for index_name, columns in QUESTION_TAG_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON question_tags "
                f"({', '.join(columns)})"
            )

    seeded = _backfill_question_tags(connection)
    return {
        "added_question_tags": int(not table_exists),
        "seeded_question_tags": seeded,
    }


def _backfill_question_tags(connection) -> int:
    """Seed chapter tags from legacy 册/章/节 columns (idempotent)."""

    from mathbank.tags import resolve_legacy_code

    rows = connection.exec_driver_sql(
        "SELECT qc.question_id, qc.compulsory, qc.chapter, qc.knowledge "
        "FROM question_curriculums AS qc "
        "JOIN questions AS q ON q.id = qc.question_id"
    ).fetchall()
    inserted = 0
    for question_id, compulsory, chapter, knowledge in rows:
        code = resolve_legacy_code(compulsory, chapter, knowledge)
        if not code:
            continue
        exists = connection.exec_driver_sql(
            "SELECT 1 FROM question_tags "
            "WHERE question_id = ? AND dim = 'chapter' AND code = ?",
            (question_id, code),
        ).first()
        if exists:
            continue
        connection.exec_driver_sql(
            "INSERT INTO question_tags (question_id, dim, code) VALUES (?, 'chapter', ?)",
            (question_id, code),
        )
        inserted += 1
    return inserted


QUESTION_RELATION_INDEXES = {
    "idx_qrel_question": ("question_id",),
    "idx_qrel_related": ("related_id",),
    "idx_qrel_code": ("relation_code",),
}

#: Relation code written by the legacy ``association_group_id`` backfill.  It is
#: the only code the backfill owns, so every probe and insert below is scoped to
#: it instead of the whole (question_id, related_id) pair.
LEGACY_RELATION_CODE = "legacy_group"
LEGACY_RELATION_LABEL = "题组"

#: Tables that ``_ensure_*_table`` helpers can create on demand, so a missing
#: one must not abort startup before the repair pass has had a chance to run.
SELF_HEALING_TABLES = frozenset({"question_relations", "question_sub_knowledge"})


def _ensure_question_relations_table(connection) -> dict[str, int]:
    """Create the typed question-relation table and fold legacy groups into it.

    The table/indexes are created idempotently.  Once the legacy groups have
    been derived into ``question_relations`` the source column is cleared, so
    the two tracks can never drift apart again: ``question_relations`` becomes
    the single source of truth and ``questions.association_group_id`` is left
    empty forever (the column itself is kept only so older builds that still
    read it keep working).

    The fold is a *move* rather than a copy.  A deletion made through the
    relation API is therefore final: nothing re-derives it on the next startup.
    """

    table_exists = bool(
        connection.exec_driver_sql(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='question_relations'"
        ).first()
    )
    if not table_exists:
        connection.exec_driver_sql(
            """
            CREATE TABLE question_relations (
                id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                related_id INTEGER NOT NULL,
                relation_code VARCHAR(60) NOT NULL,
                relation_label VARCHAR(120) NOT NULL DEFAULT '',
                note TEXT DEFAULT '',
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT uq_question_relation
                    UNIQUE (question_id, related_id, relation_code),
                CONSTRAINT ck_question_relation_order CHECK (question_id < related_id),
                CONSTRAINT fk_qrel_question
                    FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
                CONSTRAINT fk_qrel_related
                    FOREIGN KEY(related_id) REFERENCES questions(id) ON DELETE CASCADE
            )
            """
        )
        for index_name, columns in QUESTION_RELATION_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX {index_name} ON question_relations "
                f"({', '.join(columns)})"
            )
    else:
        for index_name, columns in QUESTION_RELATION_INDEXES.items():
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON question_relations "
                f"({', '.join(columns)})"
            )

    # The backfill is deliberately unconditional rather than gated on
    # ``table_exists``.  It is idempotent, and running it against an
    # already-current database is exactly what repairs a migration that was
    # interrupted between CREATE TABLE and INSERT.  Gating it on table creation
    # would strand those databases with a permanently empty relation index.
    seeded = _backfill_question_relations(connection)
    # Fold (not copy): clear the source column only after the derived rows are
    # safely inserted, inside the caller's transaction.
    cleared = _clear_association_groups(connection)
    return {
        "added_question_relations": int(not table_exists),
        "backfilled_question_relations": seeded,
        "cleared_association_groups": cleared,
    }


def _clear_association_groups(connection) -> int:
    """Blank the legacy ``association_group_id`` column (idempotent).

    Called straight after :func:`_backfill_question_relations` so the legacy
    groups have already been materialised as ``legacy_group`` relation rows.
    Blanking the column is what makes the migration single-track: later runs
    find nothing to derive, and the relation API stays the only writer.
    """

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    if "association_group_id" not in columns:
        return 0

    cursor = connection.exec_driver_sql(
        "UPDATE questions SET association_group_id = '' "
        "WHERE association_group_id IS NOT NULL AND association_group_id <> ''"
    )
    return int(cursor.rowcount or 0)


def _backfill_question_relations(connection) -> int:
    """Derive pairwise relations from legacy ``association_group_id`` (idempotent).

    The scan is strictly read-only for ``questions``: it only inserts the
    missing ``legacy_group`` rows into ``question_relations``.  It returns the
    number of rows actually inserted so repeated calls report zero.
    """

    columns = {
        row[1]
        for row in connection.exec_driver_sql(
            'PRAGMA table_info("questions")'
        ).fetchall()
    }
    if "association_group_id" not in columns:
        # Very old databases upgraded outside init_db may not have added this
        # column yet; there is no legacy grouping to migrate in that case.
        return 0

    groups = connection.exec_driver_sql(
        "SELECT DISTINCT association_group_id FROM questions "
        "WHERE association_group_id IS NOT NULL AND association_group_id <> ''"
    ).fetchall()

    inserted = 0
    for (group_id,) in groups:
        member_ids = [
            int(row[0])
            for row in connection.exec_driver_sql(
                "SELECT id FROM questions WHERE association_group_id = ? "
                "ORDER BY id ASC",
                (group_id,),
            ).fetchall()
        ]
        # Every unordered pair (i < j) inside the group becomes one row.  The
        # ascending id order satisfies the CHECK (question_id < related_id).
        for i in range(len(member_ids)):
            for j in range(i + 1, len(member_ids)):
                left, right = member_ids[i], member_ids[j]
                if left == right:
                    continue
                # The pair must be probed for the exact code we are about to
                # insert.  The table is UNIQUE (question_id, related_id,
                # relation_code), so a pair carrying only a typed relation
                # would otherwise look "already present" and silently swallow
                # the legacy row -- while a genuinely deleted legacy row would
                # still be rejected.  Probing our own code keeps the check
                # aligned with both the constraint and the insert below.
                exists = connection.exec_driver_sql(
                    "SELECT 1 FROM question_relations "
                    "WHERE question_id = ? AND related_id = ? AND relation_code = ?",
                    (left, right, LEGACY_RELATION_CODE),
                ).first()
                if exists:
                    continue
                connection.exec_driver_sql(
                    "INSERT INTO question_relations "
                    "(question_id, related_id, relation_code, relation_label) "
                    "VALUES (?, ?, ?, ?)",
                    (left, right, LEGACY_RELATION_CODE, LEGACY_RELATION_LABEL),
                )
                inserted += 1
    return inserted


#: v13 专题工坊建表语句（纯增量；与 database.py 的 ORM 模型逐列对齐）。
_TOPIC_TABLE_DDL = {
    "topics": """
        CREATE TABLE topics (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            title VARCHAR(200) NOT NULL,
            topic_type VARCHAR(30) NOT NULL DEFAULT 'knowledge',
            description TEXT NOT NULL DEFAULT '',
            view_mode VARCHAR(20) NOT NULL DEFAULT 'linear',
            parent_topic_id INTEGER DEFAULT NULL,
            is_template BOOLEAN NOT NULL DEFAULT 0,
            status VARCHAR(20) NOT NULL DEFAULT 'active',
            usage_count INTEGER NOT NULL DEFAULT 0,
            last_used_at DATETIME DEFAULT NULL,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT fk_topics_parent FOREIGN KEY(parent_topic_id) REFERENCES topics(id) ON DELETE RESTRICT
        )
    """,
    "topic_groups": """
        CREATE TABLE topic_groups (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            title VARCHAR(120) NOT NULL DEFAULT '',
            position INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_topic_group_order UNIQUE (topic_id, position),
            CONSTRAINT ck_topic_group_position CHECK (position >= 0),
            CONSTRAINT fk_topic_groups_topic FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
        )
    """,
    "topic_items": """
        CREATE TABLE topic_items (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            group_id INTEGER DEFAULT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_topic_item_question UNIQUE (topic_id, question_id),
            CONSTRAINT uq_topic_item_order UNIQUE (topic_id, position),
            CONSTRAINT ck_topic_item_position CHECK (position >= 0),
            CONSTRAINT fk_topic_items_topic FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            CONSTRAINT fk_topic_items_question FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
            CONSTRAINT fk_topic_items_group FOREIGN KEY(group_id) REFERENCES topic_groups(id) ON DELETE SET NULL
        )
    """,
    "topic_versions": """
        CREATE TABLE topic_versions (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            topic_id INTEGER NOT NULL,
            version_no INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            item_count INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_topic_version_no UNIQUE (topic_id, version_no),
            CONSTRAINT ck_topic_version_no_positive CHECK (version_no >= 1),
            CONSTRAINT ck_topic_version_item_count CHECK (item_count >= 0),
            CONSTRAINT fk_topic_versions_topic FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE CASCADE
        )
    """,
    "topic_links": """
        CREATE TABLE topic_links (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            source_topic_id INTEGER NOT NULL,
            target_topic_id INTEGER NOT NULL,
            link_code VARCHAR(60) NOT NULL,
            link_label VARCHAR(120) NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT uq_topic_link UNIQUE (source_topic_id, target_topic_id, link_code),
            CONSTRAINT ck_topic_link_order CHECK (source_topic_id <> target_topic_id),
            CONSTRAINT fk_topic_links_source FOREIGN KEY(source_topic_id) REFERENCES topics(id) ON DELETE CASCADE,
            CONSTRAINT fk_topic_links_target FOREIGN KEY(target_topic_id) REFERENCES topics(id) ON DELETE CASCADE
        )
    """,
    "question_usage_stats": """
        CREATE TABLE question_usage_stats (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            topic_id INTEGER DEFAULT NULL,
            paper_id INTEGER DEFAULT NULL,
            record_kind VARCHAR(20) NOT NULL DEFAULT 'manual',
            correct_rate REAL DEFAULT NULL,
            avg_seconds INTEGER DEFAULT NULL,
            sample_size INTEGER NOT NULL DEFAULT 0,
            note TEXT NOT NULL DEFAULT '',
            recorded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            CONSTRAINT ck_qus_rate CHECK (correct_rate IS NULL OR (correct_rate BETWEEN 0 AND 1)),
            CONSTRAINT ck_qus_seconds CHECK (avg_seconds IS NULL OR avg_seconds >= 0),
            CONSTRAINT ck_qus_sample CHECK (sample_size >= 0),
            CONSTRAINT fk_qus_question FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE,
            CONSTRAINT fk_qus_topic FOREIGN KEY(topic_id) REFERENCES topics(id) ON DELETE SET NULL,
            CONSTRAINT fk_qus_paper FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE SET NULL
        )
    """,
}

#: 每张专题表的普通索引（不含 UNIQUE 约束自建索引与部分唯一索引）。
_TOPIC_TABLE_INDEXES = {
    "topics": {
        "idx_topics_parent": ("parent_topic_id",),
        "idx_topics_type": ("topic_type",),
        "idx_topics_status": ("status",),
    },
    "topic_groups": {
        "idx_topic_groups_topic": ("topic_id",),
    },
    "topic_items": {
        "idx_topic_items_topic": ("topic_id",),
        "idx_topic_items_question": ("question_id",),
        "idx_topic_items_group": ("group_id",),
    },
    "topic_versions": {
        "idx_topic_versions_topic": ("topic_id",),
    },
    "topic_links": {
        "idx_topic_links_source": ("source_topic_id",),
        "idx_topic_links_target": ("target_topic_id",),
        "idx_topic_links_code": ("link_code",),
    },
    "question_usage_stats": {
        "idx_qus_question_time": ("question_id", "recorded_at"),
        "idx_qus_topic": ("topic_id",),
    },
}

#: 同一试卷讲评对同一题只允许一条反馈（NULL 不参与；手工记录可追加成趋势）。
_TOPIC_TABLE_PARTIAL_INDEXES = {
    "question_usage_stats": {
        "uq_qus_paper_question": (
            ("paper_id", "question_id"),
            "paper_id IS NOT NULL AND record_kind = 'paper_review'",
        ),
    },
}

#: 每张专题表的列集合（id 之外全部 NOT NULL）。
_TOPIC_TABLE_COLUMNS = {
    "topics": (
        "id", "title", "topic_type", "description", "view_mode", "parent_topic_id",
        "is_template", "status", "usage_count", "last_used_at", "created_at", "updated_at",
    ),
    "topic_groups": ("id", "topic_id", "title", "position", "note", "created_at"),
    "topic_items": ("id", "topic_id", "question_id", "group_id", "position", "note", "created_at"),
    "topic_versions": ("id", "topic_id", "version_no", "snapshot_json", "note", "item_count", "created_at"),
    "topic_links": ("id", "source_topic_id", "target_topic_id", "link_code", "link_label", "note", "created_at"),
    "question_usage_stats": (
        "id", "question_id", "topic_id", "paper_id", "record_kind", "correct_rate",
        "avg_seconds", "sample_size", "note", "recorded_at",
    ),
}

#: 每张专题表允许为 NULL 的列（其余非 id 列必须 NOT NULL）。
_TOPIC_TABLE_NULLABLE_COLUMNS = {
    "topics": ("parent_topic_id", "last_used_at"),
    "topic_groups": (),
    "topic_items": ("group_id",),
    "topic_versions": (),
    "topic_links": (),
    "question_usage_stats": ("topic_id", "paper_id", "correct_rate", "avg_seconds"),
}

#: 每张专题表必须存在的外键动作：(table, from_column) -> (parent_table, parent_column, on_delete)。
_TOPIC_TABLE_FOREIGN_KEYS = {
    "topics": {("topics", "parent_topic_id"): ("topics", "id", "RESTRICT")},
    "topic_groups": {("topic_groups", "topic_id"): ("topics", "id", "CASCADE")},
    "topic_items": {
        ("topic_items", "topic_id"): ("topics", "id", "CASCADE"),
        ("topic_items", "question_id"): ("questions", "id", "CASCADE"),
        ("topic_items", "group_id"): ("topic_groups", "id", "SET NULL"),
    },
    "topic_versions": {("topic_versions", "topic_id"): ("topics", "id", "CASCADE")},
    "topic_links": {
        ("topic_links", "source_topic_id"): ("topics", "id", "CASCADE"),
        ("topic_links", "target_topic_id"): ("topics", "id", "CASCADE"),
    },
    "question_usage_stats": {
        ("question_usage_stats", "question_id"): ("questions", "id", "CASCADE"),
        ("question_usage_stats", "topic_id"): ("topics", "id", "SET NULL"),
        ("question_usage_stats", "paper_id"): ("papers", "id", "SET NULL"),
    },
}

#: 每张专题表必须存在的 UNIQUE 约束列组（含顺序）。
_TOPIC_TABLE_UNIQUE_CONSTRAINTS = {
    "topics": (),
    "topic_groups": (("topic_id", "position"),),
    "topic_items": (("topic_id", "question_id"), ("topic_id", "position")),
    "topic_versions": (("topic_id", "version_no"),),
    "topic_links": (("source_topic_id", "target_topic_id", "link_code"),),
    "question_usage_stats": (),
}

#: 每张专题表必须存在的 CHECK 约束名（防手改库把约束抹掉）。
_TOPIC_TABLE_CHECK_CONSTRAINTS = {
    "topics": (),
    "topic_groups": ("ck_topic_group_position",),
    "topic_items": ("ck_topic_item_position",),
    "topic_versions": ("ck_topic_version_no_positive", "ck_topic_version_item_count"),
    "topic_links": ("ck_topic_link_order",),
    "question_usage_stats": ("ck_qus_rate", "ck_qus_seconds", "ck_qus_sample"),
}


def _ensure_topic_tables(connection) -> dict[str, int]:
    """v13 adds the six topic-studio tables; purely additive, no backfill."""

    stats: dict[str, int] = {}
    for table, ddl in _TOPIC_TABLE_DDL.items():
        exists = bool(connection.exec_driver_sql(
            f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table}'"
        ).first())
        if not exists:
            connection.exec_driver_sql(ddl)
            stats[f"added_{table}"] = 1
        for index_name, columns in _TOPIC_TABLE_INDEXES[table].items():
            connection.exec_driver_sql(
                f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({', '.join(columns)})"
            )
        for index_name, (columns, where_clause) in _TOPIC_TABLE_PARTIAL_INDEXES.get(table, {}).items():
            connection.exec_driver_sql(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} "
                f"({', '.join(columns)}) WHERE {where_clause}"
            )
    _validate_topic_tables(connection)
    return stats


def _validate_topic_tables(connection) -> None:
    """Fail closed when any topic table is missing, damaged or drifted."""

    table_names = {
        row[0]
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing_tables = [table for table in TOPIC_TABLES if table not in table_names]
    if missing_tables:
        raise RuntimeError(
            "检测到专题表缺失: " + ", ".join(missing_tables)
            + "。专题数据为用户原创教学内容，不会自动重建；请从 data_backup 恢复备份后重启。"
        )

    for table in TOPIC_TABLES:
        info = {
            row[1]: row
            for row in connection.exec_driver_sql(f'PRAGMA table_info("{table}")').fetchall()
        }
        expected_columns = set(_TOPIC_TABLE_COLUMNS[table])
        if set(info) != expected_columns:
            raise RuntimeError(
                f"数据库表 {table} 字段集合与 v13 结构不符: "
                f"缺少 {sorted(expected_columns - set(info))}，多出 {sorted(set(info) - expected_columns)}"
            )
        for column in _TOPIC_TABLE_COLUMNS[table]:
            if column == "id":
                if int(info[column][5]) != 1:
                    raise RuntimeError(f"{table} 主键结构异常")
                continue
            if int(info[column][5]) != 0:
                raise RuntimeError(f"{table} 字段 {column} 不应当主键")
            if column in _TOPIC_TABLE_NULLABLE_COLUMNS[table]:
                continue
            if int(info[column][3]) != 1:
                raise RuntimeError(f"数据库表 {table} 字段 {column} 缺少 NOT NULL 约束")

        foreign_keys = connection.exec_driver_sql(
            f'PRAGMA foreign_key_list("{table}")'
        ).fetchall()
        for (from_table, from_column), (parent_table, parent_column, action) in (
            _TOPIC_TABLE_FOREIGN_KEYS[table].items()
        ):
            if not any(
                row[2] == parent_table
                and row[3] == from_column
                and row[4] == parent_column
                and str(row[6]).upper() == action
                for row in foreign_keys
            ):
                raise RuntimeError(
                    f"数据库表 {table} 缺少外键 {from_column} -> "
                    f"{parent_table}.{parent_column} ON DELETE {action}"
                )

        index_rows = connection.exec_driver_sql(f'PRAGMA index_list("{table}")').fetchall()
        index_columns = {
            tuple(
                entry[2]
                for entry in connection.exec_driver_sql(
                    f'PRAGMA index_info("{row[1]}")'
                ).fetchall()
            ): row
            for row in index_rows
        }
        for columns in _TOPIC_TABLE_UNIQUE_CONSTRAINTS[table]:
            row = index_columns.get(columns)
            if row is None or int(row[2]) != 1 or str(row[3]).upper() != "U":
                raise RuntimeError(
                    f"数据库表 {table} 缺少唯一约束 {columns}"
                )
        for index_name, columns in _TOPIC_TABLE_INDEXES[table].items():
            row = index_columns.get(columns)
            if row is None:
                raise RuntimeError(f"数据库表 {table} 缺少索引 {index_name}")
            if int(row[2]) == 1 and str(row[3]).upper() == "U":
                raise RuntimeError(f"数据库表 {table} 索引 {index_name} 不应当唯一")
        for index_name, (columns, where_clause) in (
            _TOPIC_TABLE_PARTIAL_INDEXES.get(table, {}).items()
        ):
            row = index_columns.get(columns)
            if row is None:
                raise RuntimeError(f"数据库表 {table} 缺少部分唯一索引 {index_name}")
            if int(row[2]) != 1 or len(row) < 5 or int(row[4]) != 1:
                raise RuntimeError(f"数据库表 {table} 部分唯一索引 {index_name} 结构异常")
            actual_where = connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
            ).scalar()
            if not actual_where or where_clause not in str(actual_where):
                raise RuntimeError(f"数据库表 {table} 部分唯一索引 {index_name} 条件不符")

        table_sql = str(connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).scalar() or "")
        for constraint_name in _TOPIC_TABLE_CHECK_CONSTRAINTS[table]:
            if constraint_name not in table_sql:
                raise RuntimeError(f"数据库表 {table} 缺少 CHECK 约束 {constraint_name}")

        violations = connection.exec_driver_sql(f"PRAGMA foreign_key_check({table})").fetchall()
        if violations:
            raise RuntimeError(f"数据库表 {table} 存在外键异常: {violations[:3]}")


def _rebuild_relationship_tables(engine: Engine) -> dict[str, int]:
    """Rebuild relation tables with real FKs while repairing legacy drift."""

    stats: dict[str, int] = {}
    # AUTOCOMMIT lets us issue an explicit BEGIN IMMEDIATE.  Relying on the
    # sqlite3 driver's implicit transaction behavior is unsafe for DDL because
    # older driver modes may otherwise auto-commit CREATE/DROP statements.
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            before_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            before_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS question_curriculums__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE question_curriculums__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    question_id INTEGER NOT NULL,
                    version_code VARCHAR(50) NOT NULL,
                    compulsory VARCHAR(100) DEFAULT '',
                    chapter VARCHAR(100) DEFAULT '',
                    knowledge VARCHAR(100) DEFAULT '',
                    CONSTRAINT uq_question_curriculum_version
                        UNIQUE (question_id, version_code),
                    CONSTRAINT fk_question_curriculums_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO question_curriculums__new
                    (id, question_id, version_code, compulsory, chapter, knowledge)
                SELECT qc.id, qc.question_id, qc.version_code,
                       qc.compulsory, qc.chapter, qc.knowledge
                FROM question_curriculums AS qc
                JOIN questions AS q ON q.id = qc.question_id
                JOIN (
                    SELECT question_id, version_code, MAX(id) AS keep_id
                    FROM question_curriculums
                    GROUP BY question_id, version_code
                ) AS newest ON newest.keep_id = qc.id
                """
            )
            connection.exec_driver_sql("DROP TABLE question_curriculums")
            connection.exec_driver_sql(
                "ALTER TABLE question_curriculums__new RENAME TO question_curriculums"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_lookup "
                "ON question_curriculums (version_code, compulsory, chapter, knowledge)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_question_curriculums_qid "
                "ON question_curriculums (question_id)"
            )

            connection.exec_driver_sql("DROP TABLE IF EXISTS paper_questions__new")
            connection.exec_driver_sql(
                """
                CREATE TABLE paper_questions__new (
                    id INTEGER NOT NULL PRIMARY KEY,
                    paper_id INTEGER NOT NULL,
                    question_id INTEGER NOT NULL,
                    order_index INTEGER NOT NULL DEFAULT 0,
                    score INTEGER NOT NULL DEFAULT 5 CHECK (score >= 0),
                    CONSTRAINT uq_paper_question_order UNIQUE (paper_id, order_index),
                    CONSTRAINT fk_paper_questions_paper
                        FOREIGN KEY(paper_id) REFERENCES papers(id) ON DELETE CASCADE,
                    CONSTRAINT fk_paper_questions_question
                        FOREIGN KEY(question_id) REFERENCES questions(id) ON DELETE CASCADE
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO paper_questions__new
                    (id, paper_id, question_id, order_index, score)
                SELECT pq.id, pq.paper_id, pq.question_id,
                       COALESCE(pq.order_index, 0),
                       CASE WHEN pq.score IS NULL OR pq.score < 0 THEN 0 ELSE pq.score END
                FROM paper_questions AS pq
                JOIN papers AS p ON p.id = pq.paper_id
                JOIN questions AS q ON q.id = pq.question_id
                JOIN (
                    SELECT paper_id, COALESCE(order_index, 0), MAX(id) AS keep_id
                    FROM paper_questions
                    GROUP BY paper_id, COALESCE(order_index, 0)
                ) AS newest ON newest.keep_id = pq.id
                """
            )
            connection.exec_driver_sql("DROP TABLE paper_questions")
            connection.exec_driver_sql(
                "ALTER TABLE paper_questions__new RENAME TO paper_questions"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_paper_id ON paper_questions (paper_id)"
            )
            connection.exec_driver_sql(
                "CREATE INDEX idx_paper_questions_question_id ON paper_questions (question_id)"
            )
            connection.exec_driver_sql(
                """
                UPDATE papers
                SET total_score = COALESCE(
                    (
                        SELECT SUM(pq.score)
                        FROM paper_questions AS pq
                        WHERE pq.paper_id = papers.id
                    ),
                    0
                )
                """
            )

            remaining_curriculums = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM question_curriculums").scalar_one()
            )
            remaining_paper_questions = int(
                connection.exec_driver_sql("SELECT COUNT(*) FROM paper_questions").scalar_one()
            )
            tikz_column_stats = _ensure_tikz_asset_columns(connection)
            figure_layout_stats = _ensure_figure_layout_columns(connection)
            sub_knowledge_stats = _ensure_sub_knowledge_columns(connection)
            added_question_fingerprints = _ensure_question_fingerprint_table(connection)
            tag_stats = _ensure_question_tags_table(connection)
            relation_stats = _ensure_question_relations_table(connection)
            topic_stats = _ensure_topic_tables(connection)
            _validate_figure_layout_schema(connection)

            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"迁移后仍存在外键异常: {violations[:5]}")

            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            stats = {
                "removed_question_curriculums": before_curriculums - remaining_curriculums,
                "removed_paper_questions": before_paper_questions - remaining_paper_questions,
                "added_question_fingerprints": added_question_fingerprints,
                **tikz_column_stats,
                **figure_layout_stats,
                **tag_stats,
                **relation_stats,
                **sub_knowledge_stats,
                **topic_stats,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise
        finally:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            enabled = int(connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one())
            if enabled != 1:
                raise RuntimeError("迁移连接未能恢复 SQLite 外键检查")
    return stats


def _upgrade_derived_schema(engine: Engine) -> dict[str, int]:
    """Upgrade v2-v5 databases with TikZ fields and the current derived table."""

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            stats = _ensure_tikz_asset_columns(connection)
            stats.update(_ensure_figure_layout_columns(connection))
            stats.update(_ensure_sub_knowledge_columns(connection))
            stats["added_question_fingerprints"] = (
                _ensure_question_fingerprint_table(connection)
            )
            stats.update(_ensure_question_tags_table(connection))
            stats.update(_ensure_question_relations_table(connection))
            stats.update(_ensure_topic_tables(connection))
            _validate_figure_layout_schema(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return stats
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def _upgrade_v6_or_v7_fingerprint_schema(
    engine: Engine,
    *,
    from_version: int,
) -> dict[str, int]:
    """Upgrade v6/v7 indexes, v8 text bands, and the v9 figure layout."""

    if from_version not in {6, 7}:
        raise ValueError(f"unsupported fingerprint schema upgrade: {from_version}")

    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        transaction_started = False
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            transaction_started = True
            _validate_question_fingerprint_schema(
                connection,
                expected_columns=(
                    V6_QUESTION_FINGERPRINT_COLUMNS
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_COLUMNS
                ),
                expected_indexes=(
                    V6_QUESTION_FINGERPRINT_INDEXES
                    if from_version == 6
                    else V7_QUESTION_FINGERPRINT_INDEXES
                ),
            )
            rebuilt_indexes = 0
            if from_version == 6:
                for index_name, columns in V7_QUESTION_FINGERPRINT_INDEXES.items():
                    if index_name == "idx_question_fingerprints_exact":
                        continue
                    connection.exec_driver_sql(f'DROP INDEX "{index_name}"')
                    connection.exec_driver_sql(
                        f'CREATE INDEX "{index_name}" ON question_fingerprints '
                        f"({', '.join(columns)})"
                    )
                    rebuilt_indexes += 1

            for band_no in range(8):
                connection.exec_driver_sql(
                    "ALTER TABLE question_fingerprints "
                    f"ADD COLUMN text_band{band_no} VARCHAR(16) "
                    "NOT NULL DEFAULT ''"
                )

            added_text_indexes = 0
            for band_no in range(8):
                index_name = f"idx_question_fingerprints_text_band{band_no}"
                columns = QUESTION_FINGERPRINT_INDEXES[index_name]
                connection.exec_driver_sql(
                    f'CREATE INDEX "{index_name}" ON question_fingerprints '
                    f"({', '.join(columns)})"
                )
                added_text_indexes += 1
            _validate_question_fingerprint_schema(connection)
            figure_layout_stats = _ensure_figure_layout_columns(connection)
            tag_stats = _ensure_question_tags_table(connection)
            relation_stats = _ensure_question_relations_table(connection)
            sub_knowledge_stats = _ensure_sub_knowledge_columns(connection)
            topic_stats = _ensure_topic_tables(connection)
            _validate_figure_layout_schema(connection)
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
            connection.exec_driver_sql("COMMIT")
            transaction_started = False
            return {
                "rebuilt_question_fingerprint_indexes": rebuilt_indexes,
                "added_question_fingerprint_text_columns": 8,
                "added_question_fingerprint_text_indexes": added_text_indexes,
                **figure_layout_stats,
                **tag_stats,
                **relation_stats,
                **sub_knowledge_stats,
                **topic_stats,
            }
        except Exception:
            if transaction_started:
                connection.exec_driver_sql("ROLLBACK")
            raise


def migrate_database(
    engine: Engine,
    *,
    pre_migration_backup: Path | None = None,
) -> dict[str, object]:
    """Upgrade the connected SQLite database and fail loudly on errors."""

    current = schema_version(engine)
    if current > LATEST_SCHEMA_VERSION:
        raise RuntimeError(
            f"数据库版本 {current} 高于程序支持版本 {LATEST_SCHEMA_VERSION}，请升级程序。"
        )
    table_names = set(inspect(engine).get_table_names())
    if current == LATEST_SCHEMA_VERSION:
        # A database that already reports the current version may still predate
        # a freshly introduced derived table, so re-run the additive ensure
        # before the completeness check.  BEGIN IMMEDIATE takes the write lock
        # up front: the ensure pass below also seeds the derived legacy rows, and
        # two workers racing here would otherwise duplicate that work.
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
            try:
                _ensure_question_relations_table(connection)
                _ensure_sub_knowledge_columns(connection)
                connection.exec_driver_sql("COMMIT")
            except Exception:
                connection.exec_driver_sql("ROLLBACK")
                raise
        table_names = set(inspect(engine).get_table_names())
        if not REQUIRED_TABLES.issubset(table_names):
            missing = ", ".join(sorted(REQUIRED_TABLES - table_names))
            if set(missing.split(", ")) & set(TOPIC_TABLES):
                raise RuntimeError(
                    "检测到专题表缺失: " + missing
                    + "。专题数据为用户原创教学内容，不会自动重建；请从 data_backup 恢复备份后重启。"
                )
            raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")
        with engine.connect() as connection:
            _validate_question_fingerprint_schema(connection)
            _validate_figure_layout_schema(connection)
            _validate_sub_knowledge_schema(connection)
            _validate_topic_tables(connection)
        return {"from_version": current, "to_version": current, "backup": None}

    if not table_names:
        # A caller may version an empty database immediately before creating the
        # current ORM metadata.
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version={LATEST_SCHEMA_VERSION}")
        return {"from_version": current, "to_version": LATEST_SCHEMA_VERSION, "backup": None}
    if not LEGACY_REQUIRED_TABLES.issubset(table_names):
        missing = ", ".join(sorted(LEGACY_REQUIRED_TABLES - table_names))
        raise RuntimeError(f"数据库结构不完整，缺少必要数据表: {missing}")

    backup = pre_migration_backup or create_pre_migration_backup(
        engine, from_version=current, to_version=LATEST_SCHEMA_VERSION
    )
    if current < 2:
        stats = _rebuild_relationship_tables(engine)
    elif current in {6, 7}:
        stats = _upgrade_v6_or_v7_fingerprint_schema(
            engine,
            from_version=current,
        )
    else:
        stats = _upgrade_derived_schema(engine)
    return {
        "from_version": current,
        "to_version": LATEST_SCHEMA_VERSION,
        "backup": str(backup) if backup else None,
        **stats,
    }
