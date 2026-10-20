"""Topic studio (schema v13) coverage: migration, domain layer, API flows.

The topic studio folds six new tables (topics / topic_groups / topic_items /
topic_versions / topic_links / question_usage_stats) into the schema.  These
tests pin the whole contract:

* the v12 → v13 migration is purely additive and idempotent, and a v13
  database with the topic tables missing fails closed (they hold original
  teaching design, never silently rebuilt);
* the domain layer validates whitelists, refuses cycles and keeps snapshots
  self-contained;
* the API covers the normal teacher flows (create / combine / group /
  version / link / use / recommend / export / import) plus the boundary
  cases (duplicates, mismatched reorder lists, empty titles, self links,
  missing topics, missing local token).
"""

import datetime as dt
import json
import sqlite3
from contextlib import closing, contextmanager

import pytest
from sqlalchemy import create_engine

from main import LOCAL_TOKEN
from mathbank import database as database_module
from mathbank import db_migrations
from mathbank.database import (
    Paper,
    PaperQuestion,
    Question,
    QuestionRelation,
    QuestionTag,
    QuestionUsageStat,
    Topic,
)
from mathbank.topics import (
    DIFFICULTY_PROFILES,
    TOPIC_LINK_CODES,
    TOPIC_MAX_DEPTH,
    TOPIC_TYPES,
    TOPIC_VIEW_MODES,
    ensure_no_topic_cycle,
    normalize_difficulty_target,
    snapshot_diff_summary,
    validate_topic_link_code,
    validate_topic_status,
    validate_topic_type,
    validate_topic_view_mode,
)


@contextmanager
def _sqlite_connection(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


def _token():
    return {"X-Local-Token": LOCAL_TOKEN}


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded(db_session):
    """Five questions with mixed type / difficulty / answer coverage."""

    specs = [
        ("single_choice", "easy", "选 A"),
        ("single_choice", "medium", "选 B"),
        ("fill_in_blank", "medium", "填 2"),
        ("detailed_answer", "hard", "证明如下"),
        ("detailed_answer", "hard", ""),
    ]
    for index, (question_type, difficulty, answer) in enumerate(specs):
        db_session.add(
            Question(
                content=f"第{index + 1}题：已知 $x^2=4$，求 x 的值。",
                question_type=question_type,
                difficulty=difficulty,
                answer_markdown=answer,
                category_compulsory="必修第一册",
                category_chapter="第2章",
                category_knowledge="函数的单调性",
            )
        )
    db_session.commit()
    return [
        row.id
        for row in db_session.query(Question).order_by(Question.id.asc()).all()
    ]


def _create_topic(client, title="测试专题", **payload):
    body = {"title": title}
    body.update(payload)
    response = client.post("/api/topics", json=body, headers=_token())
    assert response.status_code == 200, response.text
    return response.json()["data"]["id"]


# --------------------------------------------------------------------------- #
# 1. Schema v13 migration
# --------------------------------------------------------------------------- #

def _create_v12_topic_free_database(path):
    """A complete v12 database: every table except the six topic tables."""

    engine = create_engine(f"sqlite:///{path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(path) as connection:
        # Drop children before parents so no FK dependency can interfere.
        for table in (
            "question_usage_stats",
            "topic_items",
            "topic_links",
            "topic_versions",
            "topic_groups",
            "topics",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("PRAGMA user_version=12")


def test_v12_to_v13_adds_six_topic_tables_and_is_idempotent(tmp_path, monkeypatch):
    database_path = tmp_path / "v12.db"
    _create_v12_topic_free_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")

    first = db_migrations.migrate_database(engine)
    assert first["from_version"] == 12
    # 合并上游 V3.0.0 后阶梯末位为 v17（上游三阶梯重编号为 v15–v17）。
    assert first["to_version"] == db_migrations.LATEST_SCHEMA_VERSION == 17
    for table in db_migrations.TOPIC_TABLES:
        assert first.get(f"added_{table}") == 1, table

    with _sqlite_connection(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert set(db_migrations.TOPIC_TABLES) <= names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17

    # A second run must be a no-op: nothing re-created, no stats reported.
    second = db_migrations.migrate_database(engine)
    assert second["from_version"] == 17
    assert second["to_version"] == 17
    for table in db_migrations.TOPIC_TABLES:
        assert f"added_{table}" not in second
    engine.dispose()


def test_v13_topic_tables_enforce_declared_constraints(tmp_path, monkeypatch):
    database_path = tmp_path / "v13.db"
    _create_v12_topic_free_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")
    db_migrations.migrate_database(engine)
    engine.dispose()

    with _sqlite_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO topics (id, title, topic_type) VALUES (1, '约束专题', 'method')"
        )
        connection.execute(
            "INSERT INTO questions (id, content) VALUES (1, '题一')"
        )
        connection.execute(
            "INSERT INTO questions (id, content) VALUES (2, '题二')"
        )
        connection.execute(
            "INSERT INTO topic_items (topic_id, question_id, position) "
            "VALUES (1, 1, 0)"
        )
        # UNIQUE(topic_id, question_id): the same question twice is refused.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO topic_items (topic_id, question_id, position) "
                "VALUES (1, 1, 1)"
            )
        # UNIQUE(topic_id, position): two items cannot share a slot.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO topic_items (topic_id, question_id, position) "
                "VALUES (1, 2, 0)"
            )
        # CHECK(position >= 0).
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO topic_items (topic_id, question_id, position) "
                "VALUES (1, 2, -1)"
            )
        # CHECK(source_topic_id <> target_topic_id) on topic_links.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO topic_links (source_topic_id, target_topic_id, link_code) "
                "VALUES (1, 1, 'prereq')"
            )


def test_v13_database_missing_topic_table_fails_closed(tmp_path, monkeypatch):
    """A v13 database without the topic tables must refuse to start."""

    database_path = tmp_path / "v13-missing-topics.db"
    _create_v12_topic_free_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")
    db_migrations.migrate_database(engine)
    engine.dispose()

    with _sqlite_connection(database_path) as connection:
        connection.execute("DROP TABLE topic_items")

    engine = create_engine(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError, match="专题表缺失"):
        db_migrations.migrate_database(engine)
    engine.dispose()


def test_init_db_refuses_missing_topic_tables_with_recovery_hint(tmp_path, monkeypatch):
    database_path = tmp_path / "init-missing-topics.db"
    _create_v12_topic_free_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")
    db_migrations.migrate_database(engine)
    engine.dispose()

    with _sqlite_connection(database_path) as connection:
        for table in ("topic_links", "topic_versions", "topic_groups"):
            connection.execute(f"DROP TABLE {table}")
        connection.execute("PRAGMA user_version=13")

    engine = create_engine(f"sqlite:///{database_path}")
    monkeypatch.setattr(database_module, "engine", engine)
    with pytest.raises(RuntimeError) as excinfo:
        database_module.init_db()
    message = str(excinfo.value)
    assert "专题表缺失" in message
    assert "data_backup" in message  # recovery guidance, not silent rebuild
    engine.dispose()


def test_topic_ddl_matches_orm_metadata(tmp_path):
    """DDL path and create_all path must produce the same table shape."""

    orm_path = tmp_path / "orm.db"
    ddl_path = tmp_path / "ddl.db"

    orm_engine = create_engine(f"sqlite:///{orm_path}")
    database_module.Base.metadata.create_all(bind=orm_engine)
    orm_engine.dispose()

    ddl_engine = create_engine(f"sqlite:///{ddl_path}")
    with ddl_engine.begin() as connection:
        for table, ddl in db_migrations._TOPIC_TABLE_DDL.items():
            connection.exec_driver_sql(ddl)
        for table, indexes in db_migrations._TOPIC_TABLE_INDEXES.items():
            for index_name, columns in indexes.items():
                connection.exec_driver_sql(
                    f"CREATE INDEX {index_name} ON {table} ({', '.join(columns)})"
                )
        for table, partials in db_migrations._TOPIC_TABLE_PARTIAL_INDEXES.items():
            for index_name, (columns, where_clause) in partials.items():
                connection.exec_driver_sql(
                    f"CREATE UNIQUE INDEX {index_name} ON {table} "
                    f"({', '.join(columns)}) WHERE {where_clause}"
                )
    ddl_engine.dispose()

    def table_info(path, table):
        with _sqlite_connection(path) as connection:
            return {
                row[1]: (row[2], row[3], row[5])
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            }

    for table in db_migrations.TOPIC_TABLES:
        orm_columns = table_info(orm_path, table)
        ddl_columns = table_info(ddl_path, table)
        assert set(orm_columns) == set(ddl_columns), table
        for column in orm_columns:
            # (declared type, NOT NULL flag, primary-key flag)
            assert orm_columns[column][1] == ddl_columns[column][1], (table, column)
            assert orm_columns[column][2] == ddl_columns[column][2], (table, column)


# --------------------------------------------------------------------------- #
# 2. Domain layer
# --------------------------------------------------------------------------- #

def test_validators_enforce_whitelists_with_custom_escape_hatch():
    for code in TOPIC_TYPES:
        assert validate_topic_type(code) == code
    for bad in ("", "nope", " Knowledge ", None):
        with pytest.raises(ValueError):
            validate_topic_type(bad)

    for mode in TOPIC_VIEW_MODES:
        assert validate_topic_view_mode(mode) == mode
    with pytest.raises(ValueError):
        validate_topic_view_mode("grid")

    assert validate_topic_status("draft") == "draft"
    with pytest.raises(ValueError):
        validate_topic_status("deleted")

    for code in TOPIC_LINK_CODES:
        assert validate_topic_link_code(code) == code
    assert validate_topic_link_code("custom:校本衔接") == "custom:校本衔接"
    for bad in ("totally-made-up", "custom:", "custom:   ", None):
        with pytest.raises(ValueError):
            validate_topic_link_code(bad)


def test_ensure_no_topic_cycle_rejects_self_descendant_and_depth(db_session):
    chain = []
    parent = None
    for index in range(TOPIC_MAX_DEPTH + 1):
        topic = Topic(title=f"第{index}层", parent_topic_id=parent)
        db_session.add(topic)
        db_session.flush()
        chain.append(topic.id)
        parent = topic.id
    db_session.commit()

    # Self-parenting is refused outright.
    with pytest.raises(ValueError, match="不能将自己设为父专题"):
        ensure_no_topic_cycle(db_session, chain[0], chain[0])
    # Pointing at a descendant would create a cycle.
    with pytest.raises(ValueError, match="会形成环"):
        ensure_no_topic_cycle(db_session, chain[0], chain[-1])
    # A legal move (deeper ancestor) passes.
    ensure_no_topic_cycle(db_session, chain[-1], chain[0])
    # One level past the depth limit is refused.
    with pytest.raises(ValueError, match="层级过深"):
        deeper = Topic(title="过深", parent_topic_id=chain[-1])
        db_session.add(deeper)
        db_session.flush()
        ensure_no_topic_cycle(db_session, deeper.id + 1, deeper.id)


def test_normalize_difficulty_target_sums_to_one():
    assert normalize_difficulty_target(None) == DIFFICULTY_PROFILES["knowledge"]
    normalized = normalize_difficulty_target({"easy": 2, "medium": 1, "hard": 1})
    assert abs(sum(normalized.values()) - 1.0) < 1e-9
    assert normalized["easy"] == 0.5
    assert normalize_difficulty_target({"easy": -5}) == DIFFICULTY_PROFILES["knowledge"]


def test_snapshot_diff_summary_reads_like_a_teacher():
    previous = {
        "title": "旧标题",
        "description": "",
        "view_mode": "linear",
        "items": [
            {"question_id": 1, "position": 0, "group_id": None, "note": ""},
            {"question_id": 2, "position": 1, "group_id": None, "note": ""},
        ],
    }
    current = {
        "title": "新标题",
        "description": "",
        "view_mode": "linear",
        "items": [
            {"question_id": 2, "position": 0, "group_id": 9, "note": "铺垫"},
            {"question_id": 3, "position": 1, "group_id": None, "note": ""},
        ],
    }
    summary = snapshot_diff_summary(previous, current)
    assert "+1 题" in summary and "-1 题" in summary
    assert "重排" in summary and "调整分组" in summary and "改批注" in summary
    assert "改标题" in summary
    assert snapshot_diff_summary(None, current) == "创建专题"
    assert snapshot_diff_summary(previous, previous) == "无实质变化"


# --------------------------------------------------------------------------- #
# 3. API: config, CRUD, filters
# --------------------------------------------------------------------------- #

def test_config_lists_topic_whitelists(client):
    response = client.get("/api/config/topic-types")
    assert response.status_code == 200
    data = response.json()["data"]
    assert [entry["code"] for entry in data["topic_types"]] == list(TOPIC_TYPES)
    assert {entry["code"] for entry in data["link_codes"]} >= set(TOPIC_LINK_CODES)
    assert [entry["code"] for entry in data["view_modes"]] == list(TOPIC_VIEW_MODES)
    assert data["difficulty_profiles"]["knowledge"] == DIFFICULTY_PROFILES["knowledge"]


def test_topic_crud_roundtrip(client, seeded):
    topic_id = _create_topic(
        client, "函数的单调性", topic_type="method", description="教学意图"
    )

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert detail["topic"]["title"] == "函数的单调性"
    assert detail["topic"]["topic_type"] == "method"
    assert detail["topic"]["status"] == "active"
    assert detail["items"] == [] and detail["groups"] == []

    updated = client.put(
        f"/api/topics/{topic_id}",
        json={
            "title": "函数的单调性（重讲）",
            "status": "draft",
            "view_mode": "grouped",
            "is_template": True,
            "description": "换了个讲法",
        },
        headers=_token(),
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "draft"
    assert updated.json()["data"]["is_template"] is True
    assert updated.json()["data"]["view_mode"] == "grouped"

    listing = client.get("/api/topics").json()["data"]
    row = next(entry for entry in listing if entry["id"] == topic_id)
    assert row["item_count"] == 0 and row["child_count"] == 0

    deleted = client.delete(f"/api/topics/{topic_id}", headers=_token())
    assert deleted.status_code == 200
    assert client.get(f"/api/topics/{topic_id}").status_code == 404


def test_topic_list_filters_keyword_and_sorting(client, seeded):
    method_id = _create_topic(client, "方法专题甲", topic_type="method")
    knowledge_id = _create_topic(client, "知识专题乙", topic_type="knowledge")
    # Status is metadata owned by PUT (the create contract only validates
    # title/type/parent), so a draft starts life active and is then archived.
    draft_id = _create_topic(client, "归档专题丙", topic_type="variant")
    client.put(
        f"/api/topics/{draft_id}", json={"status": "draft"}, headers=_token()
    )
    parent_id = _create_topic(client, "母专题")
    client.put(
        f"/api/topics/{parent_id}",
        json={"parent_topic_id": method_id},
        headers=_token(),
    )

    # ``type`` filters on the topic's own type; the nested child keeps its own.
    assert [row["id"] for row in client.get("/api/topics?type=method").json()["data"]] == [
        method_id,
    ]
    # Default sort is updated_at DESC: the child was re-parented last, so it
    # leads even though it was created after the knowledge topic.
    assert [
        row["id"] for row in client.get("/api/topics?type=knowledge").json()["data"]
    ] == [parent_id, knowledge_id]
    draft_rows = client.get("/api/topics?status=draft").json()["data"]
    assert [row["id"] for row in draft_rows] == [draft_id]
    keyword_rows = client.get("/api/topics?q=知识").json()["data"]
    assert [row["id"] for row in keyword_rows] == [knowledge_id]
    root_rows = client.get("/api/topics?root_only=true").json()["data"]
    assert parent_id not in {row["id"] for row in root_rows}
    template_rows = client.get("/api/topics?is_template=true").json()["data"]
    assert template_rows == []
    child_rows = client.get(f"/api/topics?parent_id={method_id}").json()["data"]
    assert [row["id"] for row in child_rows] == [parent_id]
    titled = client.get("/api/topics?sort=title").json()["data"]
    assert [row["title"] for row in titled] == sorted(row["title"] for row in titled)


def test_create_topic_rejects_bad_input(client, seeded):
    for payload in (
        {"title": "   "},
        {"title": "空类型", "topic_type": "nope"},
        {"title": "父不存在", "parent_topic_id": 999999},
        {"title": "视图非法", "view_mode": "grid"},
    ):
        response = client.post("/api/topics", json=payload, headers=_token())
        assert response.status_code == 400, payload
        assert response.json()["status"] == "error"

    long_title = client.post(
        "/api/topics", json={"title": "题" * 201}, headers=_token()
    )
    assert long_title.status_code == 400

    # Creating with question ids validates them in the same transaction.
    missing = client.post(
        "/api/topics",
        json={"title": "带题创建", "question_ids": [seeded[0], 999999]},
        headers=_token(),
    )
    assert missing.status_code == 400


def test_create_topic_with_items_notes_and_group_of(client, seeded):
    group = client.post(
        f"/api/topics", json={"title": "占位"}, headers=_token()
    ).json()["data"]["id"]
    group_id = client.post(
        f"/api/topics/{group}/groups", json={"title": "基础组"}, headers=_token()
    ).json()["data"]["id"]
    client.delete(f"/api/topics/{group}", headers=_token())

    topic_id = client.post(
        "/api/topics",
        json={
            "title": "一次建好",
            "question_ids": seeded[:2],
            "notes": {str(seeded[0]): "先讲这个"},
            "group_of": {str(seeded[1]): None},
        },
        headers=_token(),
    ).json()["data"]["id"]

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == seeded[:2]
    assert [item["position"] for item in detail["items"]] == [0, 1]
    assert detail["items"][0]["note"] == "先讲这个"
    assert detail["items"][0]["seq_num"] == 1
    assert detail["items"][0]["bank_seq_num"] == 1


def test_update_topic_rejects_cycle(client, seeded):
    parent_id = _create_topic(client, "母专题")
    child_id = _create_topic(client, "子专题", parent_topic_id=parent_id)

    self_parent = client.put(
        f"/api/topics/{parent_id}",
        json={"parent_topic_id": parent_id},
        headers=_token(),
    )
    assert self_parent.status_code == 400
    assert "不能将自己设为父专题" in self_parent.json()["message"]

    cycle = client.put(
        f"/api/topics/{parent_id}",
        json={"parent_topic_id": child_id},
        headers=_token(),
    )
    assert cycle.status_code == 400
    assert "会形成环" in cycle.json()["message"]

    bad_type = client.put(
        f"/api/topics/{parent_id}", json={"topic_type": "nope"}, headers=_token()
    )
    assert bad_type.status_code == 400


def test_delete_parent_with_children_is_refused(client, seeded):
    parent_id = _create_topic(client, "母专题")
    _create_topic(client, "子专题", parent_topic_id=parent_id)

    refused = client.delete(f"/api/topics/{parent_id}", headers=_token())
    assert refused.status_code == 400
    assert "子专题" in refused.json()["message"]

    child = client.get("/api/topics").json()["data"]
    child_id = next(row["id"] for row in child if row["title"] == "子专题")
    assert client.delete(f"/api/topics/{child_id}", headers=_token()).status_code == 200
    assert client.delete(f"/api/topics/{parent_id}", headers=_token()).status_code == 200


# --------------------------------------------------------------------------- #
# 4. API: items and groups
# --------------------------------------------------------------------------- #

def test_add_items_skips_duplicates_and_reports_counts(client, seeded):
    topic_id = _create_topic(client, "加题专题")
    first = client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": seeded[:3], "note": "整批批注"},
        headers=_token(),
    )
    assert first.status_code == 200
    assert first.json()["data"]["added"] == seeded[:3]
    assert first.json()["data"]["skipped"] == []

    second = client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": [seeded[0], seeded[3]]},
        headers=_token(),
    )
    assert second.json()["data"]["added"] == [seeded[3]]
    assert second.json()["data"]["skipped"] == [seeded[0]]

    missing = client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": [999999]},
        headers=_token(),
    )
    assert missing.status_code == 400
    assert "题目不存在" in missing.json()["message"]

    not_a_list = client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": "1,2"}, headers=_token()
    )
    assert not_a_list.status_code == 400


def test_add_items_enforces_the_300_question_ceiling(client, db_session, seeded):
    topic_id = _create_topic(client, "上限专题")
    many = []
    for index in range(300):
        question = Question(content=f"批量题 {index}", question_type="single_choice")
        db_session.add(question)
        many.append(question)
    db_session.commit()
    ids = [question.id for question in many]

    accepted = client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": ids}, headers=_token()
    )
    assert accepted.status_code == 200
    assert len(accepted.json()["data"]["added"]) == 300

    overflow = client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": [seeded[0]]},
        headers=_token(),
    )
    assert overflow.status_code == 400
    assert "300" in overflow.json()["message"]


def test_reorder_items_two_phase_and_rejects_mismatch(client, seeded):
    topic_id = _create_topic(client, "重排专题")
    client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": seeded[:4]},
        headers=_token(),
    )
    reversed_ids = list(reversed(seeded[:4]))

    response = client.put(
        f"/api/topics/{topic_id}/items/order",
        json={"question_ids": reversed_ids},
        headers=_token(),
    )
    assert response.status_code == 200

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == reversed_ids
    assert [item["position"] for item in detail["items"]] == [0, 1, 2, 3]
    assert [item["seq_num"] for item in detail["items"]] == [1, 2, 3, 4]

    for bad in (seeded[:3], seeded[:4] + [999999], "not-a-list"):
        rejected = client.put(
            f"/api/topics/{topic_id}/items/order",
            json={"question_ids": bad},
            headers=_token(),
        )
        assert rejected.status_code == 400, bad


def test_update_item_note_and_move_between_groups(client, seeded):
    topic_id = _create_topic(client, "批注专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:2]}, headers=_token()
    )
    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    item_id = detail["items"][0]["id"]

    group_id = client.post(
        f"/api/topics/{topic_id}/groups", json={"title": "第一组"}, headers=_token()
    ).json()["data"]["id"]

    noted = client.put(
        f"/api/topics/{topic_id}/items/{item_id}",
        json={"note": "板书这道"},
        headers=_token(),
    )
    assert noted.json()["data"]["note"] == "板书这道"

    moved = client.put(
        f"/api/topics/{topic_id}/items/{item_id}",
        json={"group_id": group_id},
        headers=_token(),
    )
    assert moved.json()["data"]["group_id"] == group_id

    ungrouped = client.put(
        f"/api/topics/{topic_id}/items/{item_id}",
        json={"group_id": None},
        headers=_token(),
    )
    assert ungrouped.json()["data"]["group_id"] is None

    # A group from another topic is refused.
    other_topic = _create_topic(client, "别的专题")
    other_group = client.post(
        f"/api/topics/{other_topic}/groups", json={"title": "别组"}, headers=_token()
    ).json()["data"]["id"]
    crossed = client.put(
        f"/api/topics/{topic_id}/items/{item_id}",
        json={"group_id": other_group},
        headers=_token(),
    )
    assert crossed.status_code == 400

    missing_item = client.put(
        f"/api/topics/{topic_id}/items/999999", json={"note": "x"}, headers=_token()
    )
    assert missing_item.status_code == 404


def test_remove_item_compacts_positions(client, seeded):
    topic_id = _create_topic(client, "移出专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:3]}, headers=_token()
    )
    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    middle = detail["items"][1]

    removed = client.delete(
        f"/api/topics/{topic_id}/items/{middle['id']}", headers=_token()
    )
    assert removed.status_code == 200

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == [
        seeded[0],
        seeded[2],
    ]
    assert [item["position"] for item in detail["items"]] == [0, 1]

    again = client.delete(
        f"/api/topics/{topic_id}/items/{middle['id']}", headers=_token()
    )
    assert again.status_code == 404


def test_group_crud_order_and_delete(client, seeded):
    topic_id = _create_topic(client, "分组专题")
    group_ids = []
    for title in ("甲", "乙", "丙"):
        group_ids.append(
            client.post(
                f"/api/topics/{topic_id}/groups", json={"title": title}, headers=_token()
            ).json()["data"]["id"]
        )
    assert [
        group["position"] for group in client.get(f"/api/topics/{topic_id}").json()["data"]["groups"]
    ] == [0, 1, 2]

    # Long titles are refused.
    too_long = client.post(
        f"/api/topics/{topic_id}/groups", json={"title": "组" * 121}, headers=_token()
    )
    assert too_long.status_code == 400

    # Reorder: reverse, then restore.
    client.put(
        f"/api/topics/{topic_id}/groups/order",
        json={"group_ids": list(reversed(group_ids))},
        headers=_token(),
    )
    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [group["id"] for group in detail["groups"]] == list(reversed(group_ids))
    assert [group["position"] for group in detail["groups"]] == [0, 1, 2]

    for bad in (group_ids[:2], group_ids + [999999], "nope"):
        rejected = client.put(
            f"/api/topics/{topic_id}/groups/order",
            json={"group_ids": bad},
            headers=_token(),
        )
        assert rejected.status_code == 400, bad

    # Rename.
    renamed = client.put(
        f"/api/topics/{topic_id}/groups/{group_ids[0]}",
        json={"title": "甲改", "note": "导入题"},
        headers=_token(),
    )
    assert renamed.json()["data"]["title"] == "甲改"
    assert renamed.json()["data"]["note"] == "导入题"

    # Move a question into the first group, then delete that group: the item
    # survives as ungrouped.
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:1]}, headers=_token()
    )
    item_id = client.get(f"/api/topics/{topic_id}").json()["data"]["items"][0]["id"]
    client.put(
        f"/api/topics/{topic_id}/items/{item_id}",
        json={"group_id": group_ids[0]},
        headers=_token(),
    )
    deleted = client.delete(
        f"/api/topics/{topic_id}/groups/{group_ids[0]}", headers=_token()
    )
    assert deleted.status_code == 200

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    # After the reversal the live order is 丙/乙/甲; deleting 甲 leaves the
    # first two, re-compacted to 0-based positions.
    assert [group["id"] for group in detail["groups"]] == [group_ids[2], group_ids[1]]
    assert [group["position"] for group in detail["groups"]] == [0, 1]
    assert detail["items"][0]["group_id"] is None
    assert detail["items"][0]["question_id"] == seeded[0]

    missing_group = client.put(
        f"/api/topics/{topic_id}/groups/999999", json={"title": "x"}, headers=_token()
    )
    assert missing_group.status_code == 404


# --------------------------------------------------------------------------- #
# 5. API: duplicate, versions, links
# --------------------------------------------------------------------------- #

def test_duplicate_deep_copies_groups_and_links_derived_from(client, seeded):
    topic_id = _create_topic(client, "待复制专题", topic_type="variant")
    group_id = client.post(
        f"/api/topics/{topic_id}/groups", json={"title": "原分组"}, headers=_token()
    ).json()["data"]["id"]
    client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": seeded[:2], "group_id": group_id, "note": "复制我"},
        headers=_token(),
    )

    copy_id = client.post(
        f"/api/topics/{topic_id}/duplicate", json={}, headers=_token()
    ).json()["data"]["id"]

    copy = client.get(f"/api/topics/{copy_id}").json()["data"]
    assert copy["topic"]["title"] == "待复制专题（副本）"
    assert copy["topic"]["topic_type"] == "variant"
    assert [item["question_id"] for item in copy["items"]] == seeded[:2]
    assert [item["note"] for item in copy["items"]] == ["复制我", "复制我"]
    assert len(copy["groups"]) == 1
    assert copy["groups"][0]["title"] == "原分组"
    assert copy["items"][0]["group_id"] == copy["groups"][0]["id"]

    links = client.get(f"/api/topics/{copy_id}/links").json()["data"]
    assert len(links) == 1
    assert links[0]["link_code"] == "derived_from"
    assert links[0]["direction"] == "out"
    assert links[0]["other_topic"]["id"] == topic_id

    titled = client.post(
        f"/api/topics/{topic_id}/duplicate", json={"title": "指定标题"}, headers=_token()
    ).json()["data"]["id"]
    assert client.get(f"/api/topics/{titled}").json()["data"]["topic"]["title"] == "指定标题"

    missing = client.post(f"/api/topics/999999/duplicate", json={}, headers=_token())
    assert missing.status_code == 400


def test_versions_save_restore_and_keep_last_one(client, seeded):
    topic_id = _create_topic(client, "版本专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:2]}, headers=_token()
    )

    saved = client.post(
        f"/api/topics/{topic_id}/versions", json={"note": "两题版"}, headers=_token()
    )
    assert saved.status_code == 200
    assert saved.json()["data"]["version_no"] == 1
    assert saved.json()["data"]["item_count"] == 2

    # Mutate, then roll back: restore appends a new version and replaces items.
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[2:4]}, headers=_token()
    )
    restored = client.post(
        f"/api/topics/{topic_id}/versions/1/restore", json={}, headers=_token()
    )
    assert restored.status_code == 200

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == seeded[:2]
    assert detail["version_count"] == 3  # 1: two-question, 2: four-question, 3: restored

    versions = client.get(f"/api/topics/{topic_id}/versions").json()["data"]
    assert [version["version_no"] for version in versions] == [3, 2, 1]

    # The last remaining version cannot be deleted.
    for version_no in (3, 2):
        assert client.delete(
            f"/api/topics/{topic_id}/versions/{version_no}", headers=_token()
        ).status_code == 200
    refused = client.delete(f"/api/topics/{topic_id}/versions/1", headers=_token())
    assert refused.status_code == 400
    assert "至少保留一个版本" in refused.json()["message"]

    missing_version = client.post(
        f"/api/topics/{topic_id}/versions/99/restore", json={}, headers=_token()
    )
    assert missing_version.status_code == 400


def test_links_whitelist_self_reference_and_idempotence(client, seeded):
    source_id = _create_topic(client, "源专题")
    target_id = _create_topic(client, "目标专题")

    created = client.post(
        f"/api/topics/{source_id}/links",
        json={"target_topic_id": target_id, "link_code": "prereq"},
        headers=_token(),
    )
    assert created.status_code == 200
    assert created.json()["data"]["created"] is True
    link_id = created.json()["data"]["link_id"]

    # Same triple again updates in place instead of duplicating.
    again = client.post(
        f"/api/topics/{source_id}/links",
        json={
            "target_topic_id": target_id,
            "link_code": "prereq",
            "link_label": "前置",
            "note": "先学这个",
        },
        headers=_token(),
    )
    assert again.json()["data"]["created"] is False
    assert again.json()["data"]["link_id"] == link_id

    # The target sees the same link from the other direction.
    from_target = client.get(f"/api/topics/{target_id}/links").json()["data"]
    assert len(from_target) == 1
    assert from_target[0]["direction"] == "in"
    assert from_target[0]["link_label"] == "前置"
    assert from_target[0]["note"] == "先学这个"

    # Boundary cases.
    self_link = client.post(
        f"/api/topics/{source_id}/links",
        json={"target_topic_id": source_id, "link_code": "prereq"},
        headers=_token(),
    )
    assert self_link.status_code == 400
    assert "不能与自身建立关联" in self_link.json()["message"]

    bad_code = client.post(
        f"/api/topics/{source_id}/links",
        json={"target_topic_id": target_id, "link_code": "totally-made-up"},
        headers=_token(),
    )
    assert bad_code.status_code == 400

    missing_target = client.post(
        f"/api/topics/{source_id}/links",
        json={"target_topic_id": 999999, "link_code": "prereq"},
        headers=_token(),
    )
    assert missing_target.status_code == 404

    custom = client.post(
        f"/api/topics/{source_id}/links",
        json={"target_topic_id": target_id, "link_code": "custom:校本衔接"},
        headers=_token(),
    )
    assert custom.status_code == 200

    # Config now advertises the custom code alongside the builtins.
    config_codes = {
        entry["code"]
        for entry in client.get("/api/config/topic-types").json()["data"]["link_codes"]
    }
    assert "custom:校本衔接" in config_codes

    assert client.delete(f"/api/topic-links/{link_id}", headers=_token()).status_code == 200
    remaining = client.get(f"/api/topics/{source_id}/links").json()["data"]
    assert [link["link_code"] for link in remaining] == ["custom:校本衔接"]
    assert client.delete(f"/api/topic-links/{link_id}", headers=_token()).status_code == 404


# --------------------------------------------------------------------------- #
# 6. API: usage, insights, recommendations
# --------------------------------------------------------------------------- #

def test_usage_records_stats_and_lists_history(client, seeded):
    topic_id = _create_topic(client, "学情专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:2]}, headers=_token()
    )

    recorded = client.post(
        f"/api/topics/{topic_id}/usage",
        json={
            "question_stats": [
                {"question_id": seeded[0], "correct_rate": 0.2, "avg_seconds": 90, "sample_size": 20},
                {"question_id": seeded[1], "correct_rate": 0.8, "avg_seconds": 60, "sample_size": 5},
            ]
        },
        headers=_token(),
    )
    assert recorded.status_code == 200
    assert recorded.json()["data"]["recorded"] == 2
    assert recorded.json()["data"]["usage_count"] == 1

    # A second visit appends: manual records never overwrite history.
    client.post(
        f"/api/topics/{topic_id}/usage",
        json={
            "question_stats": [
                {"question_id": seeded[0], "correct_rate": 0.9, "avg_seconds": 40, "sample_size": 20}
            ]
        },
        headers=_token(),
    )

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert detail["topic"]["usage_count"] == 2
    assert detail["topic"]["last_used_at"]

    usage = client.get(f"/api/topics/{topic_id}/usage").json()["data"]
    assert usage["usage_count"] == 2
    assert len(usage["events"]) == 3
    by_question = {entry["question_id"]: entry for entry in usage["by_question"]}
    assert len(by_question[seeded[0]]["history"]) == 2
    assert by_question[seeded[0]]["latest"]["correct_rate"] == 0.9

    # Boundary cases.
    bad_rate = client.post(
        f"/api/topics/{topic_id}/usage",
        json={"question_stats": [{"question_id": seeded[0], "correct_rate": 1.5}]},
        headers=_token(),
    )
    assert bad_rate.status_code == 400
    unknown_question = client.post(
        f"/api/topics/{topic_id}/usage",
        json={"question_stats": [{"question_id": 999999}]},
        headers=_token(),
    )
    assert unknown_question.status_code == 400
    not_a_list = client.post(
        f"/api/topics/{topic_id}/usage", json={"question_stats": "x"}, headers=_token()
    )
    assert not_a_list.status_code == 400


def test_insights_trigger_low_correct_rate_and_stay_quiet_without_data(client, seeded):
    topic_id = _create_topic(client, "洞察专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:3]}, headers=_token()
    )

    # No usage data yet: nothing is triggered, no noise.
    quiet = client.get(f"/api/topics/{topic_id}/insights").json()["data"]
    assert quiet["entries"] == []
    assert quiet["topic_level"] is None

    client.post(
        f"/api/topics/{topic_id}/usage",
        json={
            "question_stats": [
                # Low rate with a real sample: high trigger.
                {"question_id": seeded[0], "correct_rate": 0.2, "avg_seconds": 100, "sample_size": 30},
                # Low rate but tiny sample: suppressed.
                {"question_id": seeded[1], "correct_rate": 0.1, "avg_seconds": 100, "sample_size": 3},
                # Healthy: no trigger.
                {"question_id": seeded[2], "correct_rate": 0.85, "avg_seconds": 100, "sample_size": 30},
            ]
        },
        headers=_token(),
    )

    insights = client.get(f"/api/topics/{topic_id}/insights").json()["data"]
    triggered = {entry["question_id"]: entry for entry in insights["entries"]}
    assert set(triggered) == {seeded[0]}
    assert triggered[seeded[0]]["triggers"][0]["level"] == "high"
    assert "正确率" in triggered[seeded[0]]["triggers"][0]["message"]
    assert insights["topic_level"] is None
    assert insights["thresholds"]["correct_rate_high"] == 0.40


def test_insights_promote_three_high_triggers_to_topic_level(client, db_session, seeded):
    topic_id = _create_topic(client, "偏难专题")
    hard_ids = seeded[3:]
    extra = []
    for index in range(3):
        question = Question(
            content=f"附加题 {index}", question_type="single_choice", difficulty="hard"
        )
        db_session.add(question)
        extra.append(question)
    db_session.commit()
    hard_ids = hard_ids + [question.id for question in extra]
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": hard_ids}, headers=_token()
    )
    client.post(
        f"/api/topics/{topic_id}/usage",
        json={
            "question_stats": [
                {"question_id": question_id, "correct_rate": 0.15, "sample_size": 40}
                for question_id in hard_ids
            ]
        },
        headers=_token(),
    )

    insights = client.get(f"/api/topics/{topic_id}/insights").json()["data"]
    assert insights["topic_level"] is not None
    assert insights["topic_level"]["level"] == "topic"
    assert insights["topic_level"]["jump_to"] == "difficulty_balance"


def test_difficulty_balance_warns_and_suggests(client, db_session, seeded):
    topic_id = _create_topic(client, "配比专题", topic_type="knowledge")
    # Six hard questions against a knowledge profile that wants 20% hard.
    extra = []
    for index in range(4):
        question = Question(
            content=f"难题 {index}", question_type="detailed_answer", difficulty="hard"
        )
        db_session.add(question)
        extra.append(question)
    db_session.commit()
    accepted = client.post(
        f"/api/topics/{topic_id}/items",
        json={"question_ids": seeded[3:] + [question.id for question in extra]},
        headers=_token(),
    )
    assert accepted.status_code == 200

    balance = client.get(f"/api/topics/{topic_id}/insights").json()["data"]["difficulty_balance"]
    assert balance["total"] == 6
    kinds = {warning["level"]: warning["kind"] for warning in balance["warnings"]}
    assert kinds.get("easy") == "missing"
    assert kinds.get("hard") == "over"
    easy_suggestion = next(
        item for item in balance["suggestions"] if item["level"] == "easy"
    )
    assert easy_suggestion["add"] >= 1

    # Below the minimum item count the report stays silent by design.
    small_id = _create_topic(client, "太小的专题")
    client.post(
        f"/api/topics/{small_id}/items", json={"question_ids": seeded[:1]}, headers=_token()
    )
    small = client.get(f"/api/topics/{small_id}/insights").json()["data"]["difficulty_balance"]
    assert small["warnings"] == []
    assert "暂不做配比提示" in small["suppressed_reason"]


def test_recommendations_rank_related_question_with_reasons(client, db_session, seeded):
    topic_id = _create_topic(client, "推荐专题", topic_type="knowledge")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": [seeded[1]]}, headers=_token()
    )
    # A sibling variant of the seeded question, plus an unrelated stranger.
    sibling = Question(
        content="变式：已知 $x^2=9$，求 x。",
        question_type="single_choice",
        difficulty="medium",
        answer_markdown="选 A",
    )
    stranger = Question(
        content="完全无关：背诵《出师表》。",
        question_type="detailed_answer",
        difficulty="easy",
        answer_markdown="",
    )
    db_session.add_all([sibling, stranger])
    db_session.commit()
    db_session.add(
        QuestionRelation(
            question_id=min(seeded[1], sibling.id),
            related_id=max(seeded[1], sibling.id),
            relation_code="one_q_variants",
            relation_label="一题多变",
        )
    )
    db_session.commit()

    items = client.get(f"/api/topics/{topic_id}/recommendations?limit=5").json()["data"]
    ranked = {entry["question_id"]: entry for entry in items}
    assert sibling.id in ranked
    assert stranger.id not in ranked
    assert ranked[sibling.id]["score"] >= 0.45
    assert ranked[sibling.id]["reasons"], "every recommendation must be explainable"
    assert any("一题多变" in reason for reason in ranked[sibling.id]["reasons"])
    # Scores descend.
    scores = [entry["score"] for entry in items]
    assert scores == sorted(scores, reverse=True)


def test_recommendations_stay_empty_without_seeds(client, seeded):
    topic_id = _create_topic(client, "空专题")
    assert client.get(f"/api/topics/{topic_id}/recommendations").json()["data"] == []


# --------------------------------------------------------------------------- #
# 7. API: creation sources
# --------------------------------------------------------------------------- #

def test_from_tags_stratifies_by_difficulty(client, db_session, seeded):
    # Two questions per difficulty layer, all tagged with the same strategy.
    tagged = []
    for index, difficulty in enumerate(("easy", "easy", "medium", "medium", "hard", "hard")):
        question = Question(
            content=f"标签题 {index}", question_type="single_choice", difficulty=difficulty
        )
        db_session.add(question)
        db_session.flush()
        db_session.add(
            QuestionTag(question_id=question.id, dim="strategy", code="huanfa")
        )
        tagged.append(question.id)
    # Only the second hard question carries an answer, so it wins the hard slot.
    hard_second = db_session.get(Question, tagged[5])
    hard_second.answer_markdown = "有解析"
    db_session.commit()

    response = client.post(
        "/api/topics/from-tags",
        json={
            "title": "化归思想专题",
            "tag_dim": "strategy",
            "tag_codes": ["huanfa"],
            # The route clamps the request to the 5..30 window; 5 is the floor.
            "max_questions": 5,
            "topic_type": "knowledge",
        },
        headers=_token(),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["candidate_count"] == 6
    # knowledge profile: easy .30 / medium .50 / hard .20 of 5 →
    # 2 easy + 2 medium + 1 hard (the answered hard question), no spill-over.
    assert data["question_ids"] == sorted([tagged[0], tagged[1], tagged[2], tagged[3], tagged[5]])

    detail = client.get(f"/api/topics/{data['id']}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == data["question_ids"]
    assert detail["topic"]["topic_type"] == "knowledge"

    # Regeneration is stable: the same sort keys produce the same selection.
    again = client.post(
        "/api/topics/from-tags",
        json={
            "tag_dim": "strategy",
            "tag_codes": ["huanfa"],
            "max_questions": 5,
            "topic_type": "knowledge",
        },
        headers=_token(),
    )
    assert again.json()["data"]["question_ids"] == data["question_ids"]
    assert again.json()["data"]["id"] != data["id"]

    # Below-floor requests are clamped up, not rejected.
    clamped = client.post(
        "/api/topics/from-tags",
        json={"tag_dim": "strategy", "tag_codes": ["huanfa"], "max_questions": 1},
        headers=_token(),
    )
    assert clamped.status_code == 200
    assert len(clamped.json()["data"]["question_ids"]) == 5

    no_codes = client.post(
        "/api/topics/from-tags", json={"tag_codes": []}, headers=_token()
    )
    assert no_codes.status_code == 400
    bad_dim = client.post(
        "/api/topics/from-tags",
        json={"tag_dim": "nope", "tag_codes": ["huanfa"]},
        headers=_token(),
    )
    assert bad_dim.status_code == 400
    no_match = client.post(
        "/api/topics/from-tags",
        json={"tag_dim": "strategy", "tag_codes": ["nonexistent"]},
        headers=_token(),
    )
    assert no_match.status_code == 400


def _seed_paper(db_session, question_specs, title="阶段测验"):
    paper = Paper(title=title)
    db_session.add(paper)
    db_session.flush()
    for index, (question_type, chapter) in enumerate(question_specs):
        question = Question(
            content=f"试卷题 {index + 1}",
            question_type=question_type,
            category_chapter=chapter,
        )
        db_session.add(question)
        db_session.flush()
        db_session.add(
            PaperQuestion(paper_id=paper.id, question_id=question.id, order_index=index)
        )
    db_session.commit()
    return paper.id


def test_from_paper_dry_run_reports_drafts_and_orphans(client, db_session, seeded):
    # Six same-chapter questions of two types: eligible for a type-level split.
    specs = [("single_choice", "第2章")] * 4 + [("detailed_answer", "第2章")] * 2
    paper_id = _seed_paper(db_session, specs)

    dry = client.post(
        f"/api/topics/from-paper/{paper_id}", json={"dry_run": True}, headers=_token()
    )
    assert dry.status_code == 200
    data = dry.json()["data"]
    assert data["paper_title"] == "阶段测验"
    # 4 single-choice + 2 detailed: the 4-question cluster splits by type, but
    # the 2-question remainder is reported instead of silently becoming a topic.
    assert len(data["drafts"]) == 1
    assert data["drafts"][0]["question_type"] == "single_choice"
    assert len(data["drafts"][0]["question_ids"]) == 4
    assert len(data["orphans"]) == 2
    assert "不足 3 题" in data["orphans"][0]["reason"]
    # Dry run writes nothing.
    assert client.get("/api/topics").json()["data"] == []

    created = client.post(
        f"/api/topics/from-paper/{paper_id}", json={"dry_run": False}, headers=_token()
    )
    assert created.status_code == 200
    created_rows = created.json()["data"]["created"]
    assert len(created_rows) == 1
    assert created_rows[0]["title"].endswith("single_choice")

    detail = client.get(f"/api/topics/{created_rows[0]['id']}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == created_rows[0][
        "question_ids"
    ]

    empty_paper = _seed_paper(db_session, [], title="空白试卷")
    empty = client.post(
        f"/api/topics/from-paper/{empty_paper}", json={}, headers=_token()
    )
    assert empty.status_code == 400

    missing_paper = client.post(
        "/api/topics/from-paper/999999", json={}, headers=_token()
    )
    assert missing_paper.status_code == 404


def test_from_paper_small_clusters_only_report(client, db_session, seeded):
    paper_id = _seed_paper(db_session, [("single_choice", "第3章")] * 2)
    dry = client.post(
        f"/api/topics/from-paper/{paper_id}", json={"dry_run": True}, headers=_token()
    ).json()["data"]
    assert dry["drafts"] == []
    assert len(dry["orphans"]) == 2


def test_from_usage_aggregates_latest_low_correct_rate(client, db_session, seeded):
    topic_id = _create_topic(client, "再练专题")
    for index, question_id in enumerate(seeded[:2]):
        # An older healthy record followed by a newer struggling one.
        db_session.add(
            QuestionUsageStat(
                question_id=question_id,
                topic_id=topic_id,
                correct_rate=0.9,
                sample_size=40,
                recorded_at=dt.datetime(2026, 10, 1, 8, 0, 0),
            )
        )
        db_session.add(
            QuestionUsageStat(
                question_id=question_id,
                topic_id=topic_id,
                correct_rate=0.1 + index * 0.1,
                sample_size=40,
                recorded_at=dt.datetime(2026, 10, 8, 8, 0, 0),
            )
        )
    # A low rate with too small a sample must not qualify.
    db_session.add(
        QuestionUsageStat(
            question_id=seeded[2],
            topic_id=topic_id,
            correct_rate=0.05,
            sample_size=2,
            recorded_at=dt.datetime(2026, 10, 8, 9, 0, 0),
        )
    )
    db_session.commit()

    response = client.post(
        "/api/topics/from-usage",
        json={"threshold": 0.4, "min_sample": 10, "limit": 20},
        headers=_token(),
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["question_ids"] == sorted(seeded[:2])

    nothing = client.post(
        "/api/topics/from-usage", json={"threshold": 0.01}, headers=_token()
    )
    assert nothing.status_code == 400
    assert "没有符合低正确率条件" in nothing.json()["message"]


def test_from_curriculum_requires_a_filter(client, seeded):
    empty = client.post("/api/topics/from-curriculum", json={}, headers=_token())
    assert empty.status_code == 400
    assert "至少选择" in empty.json()["message"]

    created = client.post(
        "/api/topics/from-curriculum",
        json={"chapter": "第2章", "limit": 3},
        headers=_token(),
    )
    assert created.status_code == 200
    data = created.json()["data"]
    assert len(data["question_ids"]) == 3
    detail = client.get(f"/api/topics/{data['id']}").json()["data"]
    assert detail["topic"]["title"].startswith("课程专题")

    no_questions = client.post(
        "/api/topics/from-curriculum", json={"chapter": "不存在的章节"}, headers=_token()
    )
    assert no_questions.status_code == 400


# --------------------------------------------------------------------------- #
# 8. API: export / import and question deletion interplay
# --------------------------------------------------------------------------- #

def test_export_import_roundtrip_with_unresolved(client, seeded):
    topic_id = _create_topic(client, "导出专题", topic_type="method")
    group_id = client.post(
        f"/api/topics/{topic_id}/groups", json={"title": "导出组"}, headers=_token()
    ).json()["data"]["id"]
    client.post(
        f"/api/topics/{topic_id}/items",
        json={
            "question_ids": seeded[:2],
            "group_id": group_id,
            "note": "导出批注",
        },
        headers=_token(),
    )

    exported = client.get(f"/api/topics/{topic_id}/export").json()["data"]
    assert exported["format"] == "mathbank-topic"
    assert exported["topic"]["title"] == "导出专题"
    assert [item["note"] for item in exported["items"]] == ["导出批注", "导出批注"]
    assert [item["group_title"] for item in exported["items"]] == ["导出组", "导出组"]

    # Round-trip: matching stems are re-linked, the fabricated one is reported.
    exported["items"].append(
        {
            "position": 2,
            "group_title": "",
            "note": "",
            "question": {
                "id": -1,
                "content": "一道题库里不存在的题",
                "question_type": "single_choice",
                "difficulty": "easy",
                "answer_markdown": "",
                "source": "",
            },
        }
    )
    imported = client.post(
        "/api/topics/import", json={"data": exported}, headers=_token()
    )
    assert imported.status_code == 200
    data = imported.json()["data"]
    assert data["matched_count"] == 2
    assert len(data["unresolved"]) == 1
    assert data["unresolved"][0]["index"] == 2

    detail = client.get(f"/api/topics/{data['id']}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == seeded[:2]
    assert [item["note"] for item in detail["items"]] == ["导出批注", "导出批注"]
    assert len(detail["groups"]) == 1

    wrong_format = client.post(
        "/api/topics/import", json={"data": {"format": "other"}}, headers=_token()
    )
    assert wrong_format.status_code == 400
    assert "format" in wrong_format.json()["message"]

    nothing_matched = client.post(
        "/api/topics/import",
        json={
            "data": {
                "format": "mathbank-topic",
                "topic": {"title": "空对空"},
                "items": [
                    {"question": {"content": "完全不存在的一道题目内容"}}
                ],
            }
        },
        headers=_token(),
    )
    assert nothing_matched.status_code == 400
    assert "unresolved" in nothing_matched.json()["message"]


def test_delete_question_reports_affected_topics_and_compacts(
    client, db_session, seeded, monkeypatch
):
    exported_operations = []
    monkeypatch.setattr(
        "main.export_database_to_files",
        lambda: exported_operations.append("export"),
    )

    topic_id = _create_topic(client, "删题影响专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:3]}, headers=_token()
    )

    response = client.delete(f"/api/questions/{seeded[1]}", headers=_token())
    assert response.status_code == 200
    assert response.json()["removed_from_topics"] == [topic_id]
    # Deleting a question is still a database export operation.
    assert exported_operations == ["export"]

    detail = client.get(f"/api/topics/{topic_id}").json()["data"]
    assert [item["question_id"] for item in detail["items"]] == [seeded[0], seeded[2]]
    assert [item["position"] for item in detail["items"]] == [0, 1]
    assert db_session.query(Question).count() == 4


def test_topic_mutations_do_not_trigger_database_export(client, seeded, monkeypatch):
    """Topics are teaching design, not question-bank payload: no JSON churn."""

    exported = []
    monkeypatch.setattr("main.export_database_to_files", lambda: exported.append(1))

    topic_id = _create_topic(client, "静默专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": seeded[:2]}, headers=_token()
    )
    client.post(f"/api/topics/{topic_id}/groups", json={"title": "组"}, headers=_token())
    client.post(f"/api/topics/{topic_id}/versions", json={}, headers=_token())
    client.post(
        f"/api/topics/{topic_id}/usage",
        json={"question_stats": [{"question_id": seeded[0], "correct_rate": 0.5}]},
        headers=_token(),
    )
    client.post(
        f"/api/topics/{topic_id}/duplicate", json={}, headers=_token()
    )
    assert exported == []


# --------------------------------------------------------------------------- #
# 9. Boundaries: auth and missing resources
# --------------------------------------------------------------------------- #

def test_write_endpoints_require_the_local_token(client, seeded):
    response = client.post("/api/topics", json={"title": "无令牌"})
    assert response.status_code == 403
    assert response.json()["status"] == "error"

    topic_id = _create_topic(client, "只读专题")
    assert client.post(f"/api/topics/{topic_id}/items", json={"question_ids": []}).status_code == 403
    assert client.put(f"/api/topics/{topic_id}", json={"title": "x"}).status_code == 403
    assert client.delete(f"/api/topics/{topic_id}").status_code == 403
    # Reads stay open.
    assert client.get("/api/topics").status_code == 200


def test_missing_topic_returns_404(client, seeded):
    assert client.get("/api/topics/999999").status_code == 404
    assert client.put("/api/topics/999999", json={"title": "x"}, headers=_token()).status_code == 404
    assert client.delete("/api/topics/999999", headers=_token()).status_code == 404
    for path, method in (
        (f"/api/topics/999999/items", "post"),
        (f"/api/topics/999999/items/order", "put"),
        (f"/api/topics/999999/groups", "post"),
        (f"/api/topics/999999/groups/order", "put"),
        (f"/api/topics/999999/versions", "get"),
        (f"/api/topics/999999/links", "get"),
        (f"/api/topics/999999/usage", "post"),
        (f"/api/topics/999999/recommendations", "get"),
        (f"/api/topics/999999/insights", "get"),
        (f"/api/topics/999999/export", "get"),
    ):
        if method in {"post", "put"}:
            response = getattr(client, method)(path, json={}, headers=_token())
        else:
            response = getattr(client, method)(path)
        assert response.status_code == 404, path


def test_ai_recommend_reports_missing_key_instead_of_silently_degrading(
    client, db_session, seeded, monkeypatch
):
    topic_id = _create_topic(client, "AI 专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": [seeded[1]]}, headers=_token()
    )
    sibling = Question(
        content="AI 变式题", question_type="single_choice", difficulty="medium"
    )
    db_session.add(sibling)
    db_session.commit()
    db_session.add(
        QuestionRelation(
            question_id=min(seeded[1], sibling.id),
            related_id=max(seeded[1], sibling.id),
            relation_code="one_q_variants",
        )
    )
    db_session.commit()

    class _KeylessProvider:
        api_key = None
        api_base = None
        credential_label = "测试提供方"
        model_name = "test-model"

    monkeypatch.setattr(
        "mathbank.ai_providers.resolve_text_provider",
        lambda model_config, environ=None: _KeylessProvider(),
    )

    response = client.post(
        f"/api/topics/{topic_id}/ai-recommend", json={"limit": 3}, headers=_token()
    )
    assert response.status_code == 400
    message = response.json()["message"]
    assert "API Key" in message and "AI 推荐为可选功能" in message


def test_ai_recommend_without_candidates_returns_a_message(client, seeded):
    topic_id = _create_topic(client, "无候选专题")
    client.post(
        f"/api/topics/{topic_id}/items", json={"question_ids": [seeded[0]]}, headers=_token()
    )
    response = client.post(
        f"/api/topics/{topic_id}/ai-recommend", json={}, headers=_token()
    )
    assert response.status_code == 200
    assert response.json()["data"]["selected"] == []
    assert "规则引擎未找到候选题目" in response.json()["data"]["message"]
