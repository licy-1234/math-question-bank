"""GGB 动态演示资源库（schema v14）覆盖：迁移 + 领域层。

本文件钉住四类契约：

* v13 → v14 迁移**纯增量且幂等**，只加四张 GGB 表，不动任何既有表/数据；
* 自报 v14 但 GGB 表缺失时**必须 fail-closed**（教师自制的 .ggb 与题目关联是
  用户原创内容，重建空表等于静默数据丢失）；自报 v13 但专题表缺失同理；
* 领域层白名单校验、标签四维同步、版本 append-only 与回滚语义；
* 题目↔GGB 关联的排序与唯一约束。

路由层契约在 tests/test_ggb_routes.py，zip 攻击面在 tests/test_ggb_security.py。
"""

import sqlite3
from contextlib import closing, contextmanager

import pytest
from sqlalchemy import create_engine

from mathbank import database as database_module
from mathbank import db_migrations
from mathbank.database import GgbAsset, GgbTag, GgbVersion, Question, QuestionGgbLink
from mathbank import ggbs


@contextmanager
def _sqlite_connection(path):
    with closing(sqlite3.connect(path)) as connection:
        with connection:
            yield connection


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

@pytest.fixture
def seeded_questions(db_session):
    """三道题，供关联测试使用。"""

    for index in range(3):
        db_session.add(
            Question(
                content=f"第{index + 1}题：已知圆 $x^2+y^2=4$，求切线方程。",
                question_type="detailed_answer",
                difficulty="medium",
                answer_markdown="设切线方程…",
                category_compulsory="必修第二册",
                category_chapter="第2章",
                category_knowledge="圆与方程",
            )
        )
    db_session.commit()
    return [
        row.id
        for row in db_session.query(Question).order_by(Question.id.asc()).all()
    ]


def _make_asset(db, **overrides):
    payload = {
        "title": "2.5.1例3教材P99T13",
        "file_path": "/static/uploads/ggb/deadbeef.ggb",
        "file_size": 19 * 1024,
        "file_sha256": "a" * 64,
        "description": "直线与圆的位置关系",
        "tags": {"dynamic_type": ["param_slider"], "difficulty": ["medium"]},
    }
    payload.update(overrides)
    return ggbs.create_ggb_asset(db, **payload)


# --------------------------------------------------------------------------- #
# 1. Schema v14 迁移
# --------------------------------------------------------------------------- #

def _create_v13_ggb_free_database(path):
    """一个完整的 v13 库：除了四张 GGB 表什么都有。"""

    engine = create_engine(f"sqlite:///{path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(path) as connection:
        for table in (
            "ggb_tags",
            "question_ggb_links",
            "ggb_versions",
            "ggb_assets",
        ):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.execute("PRAGMA user_version=13")


def test_v13_to_v14_adds_four_ggb_tables_and_is_idempotent(tmp_path, monkeypatch):
    database_path = tmp_path / "v13.db"
    _create_v13_ggb_free_database(database_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{database_path}")

    first = db_migrations.migrate_database(engine)
    assert first["from_version"] == 13
    # 合并上游 V3.0.0 后阶梯末位为 v17（上游三阶梯重编号为 v15–v17）。
    assert first["to_version"] == db_migrations.LATEST_SCHEMA_VERSION == 17
    for table in db_migrations.GGB_TABLES:
        assert first.get(f"added_{table}") == 1, table
    # 既有专题表不得被"重建"（纯增量的证据）。
    for table in db_migrations.TOPIC_TABLES:
        assert f"added_{table}" not in first, table

    with _sqlite_connection(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert set(db_migrations.GGB_TABLES) <= names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17

    # 二次运行必须是 no-op。
    second = db_migrations.migrate_database(engine)
    assert second["from_version"] == 17
    assert second["to_version"] == 17
    for table in db_migrations.GGB_TABLES:
        assert f"added_{table}" not in second, table
    engine.dispose()


def test_v14_database_missing_ggb_table_fails_closed(tmp_path, monkeypatch):
    """v14 库缺一张 GGB 表时必须报错，且绝不自动重建。"""

    database_path = tmp_path / "v14-broken.db"
    engine = create_engine(f"sqlite:///{database_path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(database_path) as connection:
        connection.execute("DROP TABLE ggb_versions")
        connection.execute("PRAGMA user_version=14")
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")

    engine = create_engine(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError) as excinfo:
        db_migrations.migrate_database(engine)
    message = str(excinfo.value)
    assert "GGB 资源表缺失" in message
    assert "ggb_versions" in message
    assert "data_backup" in message

    with _sqlite_connection(database_path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "ggb_versions" not in names, "缺表被自动重建了，fail-closed 失效"
    engine.dispose()


def test_v13_database_missing_topic_table_still_fails_closed(tmp_path, monkeypatch):
    """v13 库缺专题表时不能被"静默补建"伪装成一次正常升级。"""

    database_path = tmp_path / "v13-no-topics.db"
    engine = create_engine(f"sqlite:///{database_path}")
    database_module.Base.metadata.create_all(bind=engine)
    engine.dispose()
    with _sqlite_connection(database_path) as connection:
        connection.execute("DROP TABLE question_usage_stats")
        connection.execute("PRAGMA user_version=13")
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")

    engine = create_engine(f"sqlite:///{database_path}")
    with pytest.raises(RuntimeError) as excinfo:
        db_migrations.migrate_database(engine)
    assert "专题表缺失" in str(excinfo.value)
    engine.dispose()


def test_ggb_tables_are_not_self_healing():
    """GGB 表刻意不进 SELF_HEALING_TABLES（与专题六表同策略）。"""

    assert not (set(db_migrations.GGB_TABLES) & db_migrations.SELF_HEALING_TABLES)
    assert set(db_migrations.GGB_TABLES) <= db_migrations.REQUIRED_TABLES


def test_orm_matches_migrated_ggb_structure(tmp_path, monkeypatch):
    """ORM 建的表必须与迁移 DDL 结构一致（校验器会逐列比对）。"""

    migrated_path = tmp_path / "migrated.db"
    _create_v13_ggb_free_database(migrated_path)
    monkeypatch.setattr(db_migrations, "SCHEMA_SNAPSHOT_DIR", tmp_path / "snap")
    engine = create_engine(f"sqlite:///{migrated_path}")
    db_migrations.migrate_database(engine)
    engine.dispose()

    orm_path = tmp_path / "orm.db"
    orm_engine = create_engine(f"sqlite:///{orm_path}")
    database_module.Base.metadata.create_all(bind=orm_engine)
    orm_engine.dispose()

    def columns(path, table):
        with closing(sqlite3.connect(path)) as connection:
            return [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]

    for table in db_migrations.GGB_TABLES:
        assert columns(migrated_path, table) == columns(orm_path, table), table


def test_migration_module_exposes_v14_metadata():
    assert db_migrations.LATEST_SCHEMA_VERSION == 17
    assert len(db_migrations.GGB_TABLES) == 4
    for table in db_migrations.GGB_TABLES:
        assert table in db_migrations._GGB_TABLE_DDL
        assert table in db_migrations._GGB_TABLE_COLUMNS
        assert table in db_migrations._GGB_TABLE_INDEXES
        assert table in db_migrations._GGB_TABLE_FOREIGN_KEYS


# --------------------------------------------------------------------------- #
# 2. 领域层：校验与标签
# --------------------------------------------------------------------------- #

def test_link_code_whitelist_and_custom_escape():
    for code in ggbs.GGB_LINK_CODES:
        assert ggbs.validate_ggb_link_code(code) == code
    assert ggbs.validate_ggb_link_code("custom:我的用法") == "custom:我的用法"
    with pytest.raises(ValueError):
        ggbs.validate_ggb_link_code("unknown")
    with pytest.raises(ValueError):
        ggbs.validate_ggb_link_code("custom:")


def test_validate_status_whitelist():
    assert ggbs.validate_ggb_status("active") == "active"
    assert ggbs.validate_ggb_status("archived") == "archived"
    with pytest.raises(ValueError):
        ggbs.validate_ggb_status("public")


def test_tag_code_whitelist_has_no_question_type():
    """标签刻意只有四维：question_type 被移除（同一 GGB 常跨题型复用）。"""

    assert ggbs.GGB_TAG_DIMS == ("knowledge", "dynamic_type", "difficulty", "custom")
    assert "question_type" not in ggbs.GGB_TAG_DIMS


def test_normalize_tags_rejects_unknown_dim_and_bad_codes():
    normalized = ggbs.normalize_ggb_tags(
        {"knowledge": ["B1-C1-S1-P2", "B1-C1-S1-P2", "  "], "difficulty": ["hard"]}
    )
    assert normalized["knowledge"] == ["B1-C1-S1-P2"]  # 去重 + 去空
    assert normalized["difficulty"] == ["hard"]
    assert normalized["custom"] == []

    with pytest.raises(ValueError):
        ggbs.normalize_ggb_tags({"question_type": ["single_choice"]})
    with pytest.raises(ValueError):
        ggbs.normalize_ggb_tags({"dynamic_type": ["不存在的类型"]})
    with pytest.raises(ValueError):
        ggbs.normalize_ggb_tags({"difficulty": ["impossible"]})


def test_normalize_tags_accepts_frontend_payload_shapes():
    """前端历史上用过三种标签形状，后端必须都接受（契约脆弱点，回归钉死）。"""

    # 规范对象形状
    assert ggbs.normalize_ggb_tags({"custom": ["阿氏圆"]})["custom"] == ["阿氏圆"]
    # 字符串数组 → 归入 custom
    assert sorted(ggbs.normalize_ggb_tags(["阿氏圆", "切线"])["custom"]) == sorted(["阿氏圆", "切线"])
    # 对象数组（前端 (g) 的 tag_code/tag_dim 形状）
    shaped = ggbs.normalize_ggb_tags(
        [{"tag_code": "阿氏圆", "tag_dim": "custom"}, {"tag_code": "trace", "tag_dim": "dynamic_type"}]
    )
    assert shaped["custom"] == ["阿氏圆"]
    assert shaped["dynamic_type"] == ["trace"]
    # 空/None 一律得空结构，不炸
    assert ggbs.normalize_ggb_tags(None)["custom"] == []
    assert ggbs.normalize_ggb_tags([{"tag_code": "  "}])["custom"] == []
    # 对象数组里的未知维度仍要报错（不放过拼写错误）
    with pytest.raises(ValueError):
        ggbs.normalize_ggb_tags([{"tag_code": "x", "tag_dim": "typo_dim"}])


def test_sync_tags_replaces_whole_set(db_session):
    ggb_id = _make_asset(db_session)
    assert ggbs.get_ggb_tags(db_session, ggb_id)["dynamic_type"] == ["param_slider"]

    ggbs.sync_ggb_tags(db_session, ggb_id, {"dynamic_type": ["trace"], "custom": ["阿氏圆"]})
    tags = ggbs.get_ggb_tags(db_session, ggb_id)
    assert tags["dynamic_type"] == ["trace"]
    assert tags["custom"] == ["阿氏圆"]

    ggbs.sync_ggb_tags(db_session, ggb_id, {})
    tags = ggbs.get_ggb_tags(db_session, ggb_id)
    assert all(value == [] for value in tags.values())
    assert db_session.query(GgbTag).filter(GgbTag.ggb_id == ggb_id).count() == 0


# --------------------------------------------------------------------------- #
# 3. 领域层：创建 / 版本 / 删除
# --------------------------------------------------------------------------- #

def test_create_asset_creates_version_one_and_tags(db_session):
    ggb_id = _make_asset(db_session)
    asset = db_session.get(GgbAsset, ggb_id)
    assert asset.current_version_no == 1
    assert asset.status == "active"
    versions = db_session.query(GgbVersion).filter(GgbVersion.ggb_id == ggb_id).all()
    assert len(versions) == 1
    assert versions[0].version_no == 1
    assert versions[0].file_path == asset.file_path  # 版本 1 直接拥有上传文件
    assert db_session.query(GgbTag).filter(GgbTag.ggb_id == ggb_id).count() == 2


def test_create_asset_rejects_empty_title_and_path(db_session):
    with pytest.raises(ValueError):
        ggbs.create_ggb_asset(db_session, title="   ", file_path="/static/uploads/ggb/a.ggb")
    with pytest.raises(ValueError):
        ggbs.create_ggb_asset(db_session, title="标题", file_path="")
    with pytest.raises(ValueError):
        ggbs.create_ggb_asset(db_session, title="x" * 201, file_path="/static/uploads/ggb/a.ggb")


def test_version_append_and_current_pointer_advance(db_session):
    ggb_id = _make_asset(db_session)

    version2 = ggbs.add_ggb_version(
        db_session, ggb_id,
        file_path="/static/uploads/ggb/versions/1/bbbb.ggb",
        file_size=22 * 1024, file_sha256="b" * 64, note="改了两条辅助线",
    )
    assert version2["version_no"] == 2
    asset = db_session.get(GgbAsset, ggb_id)
    assert asset.current_version_no == 2
    assert asset.file_path == "/static/uploads/ggb/versions/1/bbbb.ggb"
    assert asset.file_sha256 == "b" * 64
    assert len(ggbs.list_ggb_versions(db_session, ggb_id)) == 2


def test_restore_is_append_only_and_moves_pointer(db_session):
    ggb_id = _make_asset(db_session)
    ggbs.add_ggb_version(
        db_session, ggb_id,
        file_path="/static/uploads/ggb/versions/1/bbbb.ggb",
        file_size=22 * 1024, file_sha256="b" * 64,
    )
    restored = ggbs.restore_ggb_version(
        db_session, ggb_id, 1,
        file_path="/static/uploads/ggb/versions/1/cccc.ggb",
        file_size=19 * 1024, file_sha256="a" * 64,
    )
    assert restored["version_no"] == 3
    assert restored["note"] == "恢复自版本 1"
    # append-only：三个版本行都在，没有任何一行被改写或删除。
    versions = ggbs.list_ggb_versions(db_session, ggb_id)
    assert [row["version_no"] for row in versions] == [3, 2, 1]
    asset = db_session.get(GgbAsset, ggb_id)
    assert asset.current_version_no == 3
    assert asset.file_path == "/static/uploads/ggb/versions/1/cccc.ggb"


def test_restore_unknown_version_raises(db_session):
    ggb_id = _make_asset(db_session)
    with pytest.raises(ValueError):
        ggbs.restore_ggb_version(
            db_session, ggb_id, 99,
            file_path="/static/uploads/ggb/versions/1/x.ggb", file_size=1, file_sha256="c" * 64,
        )


def test_delete_version_keeps_one_and_repoints(db_session):
    ggb_id = _make_asset(db_session)
    with pytest.raises(ValueError):
        ggbs.delete_ggb_version(db_session, ggb_id, 1)  # 只剩一个版本，不许删

    ggbs.add_ggb_version(
        db_session, ggb_id,
        file_path="/static/uploads/ggb/versions/1/bbbb.ggb",
        file_size=22 * 1024, file_sha256="b" * 64,
    )
    removed = ggbs.delete_ggb_version(db_session, ggb_id, 2)
    assert removed["released_file_path"] == "/static/uploads/ggb/versions/1/bbbb.ggb"
    assert removed["current_version_no"] == 1
    asset = db_session.get(GgbAsset, ggb_id)
    assert asset.current_version_no == 1
    assert asset.file_path == "/static/uploads/ggb/deadbeef.ggb"


def test_delete_asset_requires_force_when_linked_and_returns_files(db_session, seeded_questions):
    ggb_id = _make_asset(db_session)
    ggbs.add_ggb_version(
        db_session, ggb_id,
        file_path="/static/uploads/ggb/versions/1/bbbb.ggb",
        file_size=22 * 1024, file_sha256="b" * 64,
    )
    ggbs.add_ggb_link(
        db_session, question_id=seeded_questions[0], ggb_id=ggb_id, link_code="demo"
    )

    with pytest.raises(ValueError) as excinfo:
        ggbs.delete_ggb_asset(db_session, ggb_id)
    assert "force=true" in str(excinfo.value)

    removed = ggbs.delete_ggb_asset(db_session, ggb_id, force=True)
    assert removed["links_removed"] == 1
    assert set(removed["file_paths"]) == {
        "/static/uploads/ggb/deadbeef.ggb",
        "/static/uploads/ggb/versions/1/bbbb.ggb",
    }
    assert db_session.get(GgbAsset, ggb_id) is None
    assert db_session.query(GgbVersion).filter(GgbVersion.ggb_id == ggb_id).count() == 0
    assert db_session.query(QuestionGgbLink).filter(QuestionGgbLink.ggb_id == ggb_id).count() == 0


def test_deleting_asset_cascades_in_database(db_session):
    """DB 级 CASCADE 必须真的生效（ORM 侧 passive_deletes 依赖它）。"""

    ggb_id = _make_asset(db_session)
    db_session.commit()
    db_session.execute(
        GgbVersion.__table__.delete().where(GgbVersion.ggb_id == ggb_id)
    )
    db_session.execute(GgbTag.__table__.delete().where(GgbTag.ggb_id == ggb_id))
    db_session.execute(GgbAsset.__table__.delete().where(GgbAsset.id == ggb_id))
    db_session.commit()
    assert db_session.query(GgbVersion).filter(GgbVersion.ggb_id == ggb_id).count() == 0


# --------------------------------------------------------------------------- #
# 4. 领域层：列表 / 详情 / 更新
# --------------------------------------------------------------------------- #

def test_list_filters_and_paging(db_session):
    first = _make_asset(db_session, title="阿氏圆", tags={"dynamic_type": ["trace"]})
    _make_asset(
        db_session, title="切线方程", file_path="/static/uploads/ggb/second.ggb",
        tags={"dynamic_type": ["param_slider"], "difficulty": ["hard"]},
    )

    everything = ggbs.list_ggb_assets(db_session)
    assert everything["total"] == 2
    assert len(everything["items"]) == 2
    assert all("tags" in row for row in everything["items"])

    by_keyword = ggbs.list_ggb_assets(db_session, q="阿氏")
    assert [row["id"] for row in by_keyword["items"]] == [first]

    by_tag = ggbs.list_ggb_assets(db_session, dim="dynamic_type", code="trace")
    assert [row["id"] for row in by_tag["items"]] == [first]

    paged = ggbs.list_ggb_assets(db_session, page=2, page_size=1)
    assert paged["total"] == 2
    assert len(paged["items"]) == 1

    with pytest.raises(ValueError):
        ggbs.list_ggb_assets(db_session, dim="question_type")
    with pytest.raises(ValueError):
        ggbs.list_ggb_assets(db_session, sort="unsupported")


def test_list_keyword_escapes_like_wildcards(db_session):
    _make_asset(db_session, title="百分之百%的题")
    _make_asset(db_session, title="普通题", file_path="/static/uploads/ggb/plain.ggb")
    assert ggbs.list_ggb_assets(db_session, q="%")["total"] == 1


def test_detail_includes_versions_tags_and_links(db_session, seeded_questions):
    ggb_id = _make_asset(db_session)
    ggbs.add_ggb_link(
        db_session, question_id=seeded_questions[0], ggb_id=ggb_id,
        link_code="demo", link_label="直观演示", note="拖动参数看位置关系",
    )
    detail = ggbs.get_ggb_detail(db_session, ggb_id)
    assert detail["asset"]["id"] == ggb_id
    assert detail["version_count"] == 1
    assert detail["link_count"] == 1
    assert detail["links"][0]["question_id"] == seeded_questions[0]
    assert detail["links"][0]["link_label"] == "直观演示"
    assert detail["links"][0]["question_snippet"].startswith("第1题")

    with pytest.raises(ValueError):
        ggbs.get_ggb_detail(db_session, 999999)


def test_update_asset_fields_and_status(db_session):
    ggb_id = _make_asset(db_session)
    updated = ggbs.update_ggb_asset(db_session, ggb_id, {"title": "新标题", "status": "archived"})
    assert updated["title"] == "新标题"
    assert updated["status"] == "archived"

    with pytest.raises(ValueError):
        ggbs.update_ggb_asset(db_session, ggb_id, {"status": "public"})


# --------------------------------------------------------------------------- #
# 5. 领域层：题目关联
# --------------------------------------------------------------------------- #

def test_link_positions_compact_after_removal(db_session, seeded_questions):
    ggb_a = _make_asset(db_session, title="A")
    ggb_b = _make_asset(db_session, title="B", file_path="/static/uploads/ggb/b.ggb")
    ggb_c = _make_asset(db_session, title="C", file_path="/static/uploads/ggb/c.ggb")
    question_id = seeded_questions[0]

    links = [
        ggbs.add_ggb_link(db_session, question_id=question_id, ggb_id=value, link_code="demo")
        for value in (ggb_a, ggb_b, ggb_c)
    ]
    assert [row["position"] for row in links] == [0, 1, 2]

    ggbs.remove_ggb_link(db_session, links[1]["id"])
    remaining = ggbs.list_question_ggb_links(db_session, question_id)
    assert [row["position"] for row in remaining] == [0, 1]
    assert [row["ggb_id"] for row in remaining] == [ggb_a, ggb_c]


def test_duplicate_link_is_rejected(db_session, seeded_questions):
    ggb_id = _make_asset(db_session)
    ggbs.add_ggb_link(
        db_session, question_id=seeded_questions[0], ggb_id=ggb_id, link_code="demo"
    )
    with pytest.raises(ValueError) as excinfo:
        ggbs.add_ggb_link(
            db_session, question_id=seeded_questions[0], ggb_id=ggb_id, link_code="demo"
        )
    assert "同类型关联" in str(excinfo.value)


def test_add_link_validates_question_and_asset(db_session, seeded_questions):
    ggb_id = _make_asset(db_session)
    with pytest.raises(ValueError):
        ggbs.add_ggb_link(db_session, question_id=999999, ggb_id=ggb_id, link_code="demo")
    with pytest.raises(ValueError):
        ggbs.add_ggb_link(
            db_session, question_id=seeded_questions[0], ggb_id=999999, link_code="demo"
        )


def test_only_one_default_link_per_question(db_session, seeded_questions):
    ggb_a = _make_asset(db_session, title="A")
    ggb_b = _make_asset(db_session, title="B", file_path="/static/uploads/ggb/b.ggb")
    question_id = seeded_questions[0]

    first = ggbs.add_ggb_link(
        db_session, question_id=question_id, ggb_id=ggb_a, link_code="demo", is_default=True
    )
    second = ggbs.add_ggb_link(
        db_session, question_id=question_id, ggb_id=ggb_b, link_code="demo", is_default=True
    )
    rows = {row["id"]: row for row in ggbs.list_question_ggb_links(db_session, question_id)}
    assert rows[first["id"]]["is_default"] is False
    assert rows[second["id"]]["is_default"] is True


def test_reorder_links(db_session, seeded_questions):
    ggb_a = _make_asset(db_session, title="A")
    ggb_b = _make_asset(db_session, title="B", file_path="/static/uploads/ggb/b.ggb")
    question_id = seeded_questions[0]
    links = [
        ggbs.add_ggb_link(db_session, question_id=question_id, ggb_id=value, link_code="demo")
        for value in (ggb_a, ggb_b)
    ]
    ggbs.reorder_ggb_links(db_session, question_id, [links[1]["id"], links[0]["id"]])
    reordered = ggbs.list_question_ggb_links(db_session, question_id)
    assert [row["id"] for row in reordered] == [links[1]["id"], links[0]["id"]]

    with pytest.raises(ValueError):
        ggbs.reorder_ggb_links(db_session, question_id, [links[0]["id"]])
    with pytest.raises(ValueError):
        ggbs.reorder_ggb_links(db_session, question_id, "not-a-list")


def test_update_link_fields(db_session, seeded_questions):
    ggb_id = _make_asset(db_session)
    link = ggbs.add_ggb_link(
        db_session, question_id=seeded_questions[0], ggb_id=ggb_id, link_code="demo"
    )
    updated = ggbs.update_ggb_link(
        db_session, link["id"],
        {"link_code": "steps", "link_label": "解题步骤", "note": "先联立"},
    )
    assert updated["link_code"] == "steps"
    assert updated["link_label"] == "解题步骤"
    assert updated["note"] == "先联立"

    with pytest.raises(ValueError):
        ggbs.update_ggb_link(db_session, link["id"], {"link_code": "nope"})
    with pytest.raises(ValueError):
        ggbs.update_ggb_link(db_session, 999999, {"note": "x"})


# --------------------------------------------------------------------------- #
# 6. 引用收集（防启动清理误伤）
# --------------------------------------------------------------------------- #

def test_collect_referenced_paths_covers_ggb_preview_and_versions(tmp_path, db_session):
    uploads = tmp_path / "uploads"
    (uploads / "ggb" / "versions" / "1").mkdir(parents=True)
    (uploads / "ggb" / "previews").mkdir(parents=True)
    for relative in (
        "ggb/current.ggb",
        "ggb/versions/1/old.ggb",
        "ggb/previews/shot.png",
    ):
        (uploads / relative).write_bytes(b"x")
    # 一条越界引用，必须被安全解析器拒绝并跳过。
    outside = tmp_path / "outside.ggb"
    outside.write_bytes(b"x")

    ggb_id = _make_asset(
        db_session,
        file_path="/static/uploads/ggb/current.ggb",
        preview_image_path="/static/uploads/ggb/previews/shot.png",
    )
    ggbs.add_ggb_version(
        db_session, ggb_id,
        file_path="/static/uploads/ggb/versions/1/old.ggb",
        file_size=10, file_sha256="d" * 64,
    )
    ggbs.update_ggb_asset(db_session, ggb_id, {"description": "触发一次更新"})

    resolved = ggbs.collect_referenced_ggb_paths(
        db_session, uploads_dir=uploads, url_prefix="static/uploads"
    )
    names = {path.name for path in resolved}
    assert names == {"current.ggb", "old.ggb", "shot.png"}


def test_collect_referenced_paths_skips_bad_references(tmp_path, db_session):
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    _make_asset(
        db_session,
        file_path="/static/uploads/ggb/../../escape.ggb",
        preview_image_path="/static/uploads/ggb/notes.txt",
    )
    resolved = ggbs.collect_referenced_ggb_paths(
        db_session, uploads_dir=uploads, url_prefix="static/uploads"
    )
    assert resolved == set()
