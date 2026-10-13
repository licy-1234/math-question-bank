"""tag_memory 模块与 API 的行为验证：自定义标签 / 微观方法的记忆复用。"""

import pytest

import main
from main import LOCAL_TOKEN
from mathbank import tag_memory
from mathbank.database import Question, QuestionTag

AUTH = {"X-Local-Token": LOCAL_TOKEN}


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """把记忆文件指到临时目录，测试之间互不污染。"""
    path = tmp_path / "tag_memory_test.json"
    monkeypatch.setattr(tag_memory, "_memory_path", lambda: str(path))
    return path


class TestRecordAndList:
    def test_record_creates_entries_mru_first(self):
        tag_memory.record_usage("custom", ["教材P98T4", "选必一教材103T19"])
        items = tag_memory.list_memory("custom")
        assert [item["tag"] for item in items] == ["选必一教材103T19", "教材P98T4"]
        assert all(item["use_count"] == 1 for item in items)

    def test_repeat_usage_increments_count_and_bumps_mru(self):
        tag_memory.record_usage("custom", ["旧标签"])
        tag_memory.record_usage("custom", ["新标签"])
        tag_memory.record_usage("custom", ["旧标签"])
        items = tag_memory.list_memory("custom")
        assert items[0]["tag"] == "旧标签"
        assert items[0]["use_count"] == 2
        assert items[1]["tag"] == "新标签"

    def test_cleans_and_dedupes(self):
        updated = tag_memory.record_usage("micro", ["  同构法·乘除同构 ", "同构法·乘除同构", ""])
        assert len(updated) == 1
        assert updated[0]["tag"] == "同构法·乘除同构"

    def test_unknown_dim_is_noop(self):
        assert tag_memory.record_usage("bogus", ["x"]) == []
        assert tag_memory.list_memory("bogus") == []

    def test_dimensions_are_isolated(self):
        tag_memory.record_usage("custom", ["共用名"])
        tag_memory.record_usage("micro", ["共用名"])
        assert [i["tag"] for i in tag_memory.list_memory("custom")] == ["共用名"]
        assert [i["tag"] for i in tag_memory.list_memory("micro")] == ["共用名"]
        assert tag_memory.delete_memory("custom", "共用名") is True
        assert tag_memory.list_memory("custom") == []
        assert [i["tag"] for i in tag_memory.list_memory("micro")] == ["共用名"]


class TestDeleteAndClear:
    def test_delete_missing_returns_false(self):
        assert tag_memory.delete_memory("custom", "不存在") is False

    def test_clear(self):
        tag_memory.record_usage("custom", ["a", "b"])
        assert tag_memory.clear_memory("custom") == 2
        assert tag_memory.list_memory("custom") == []


class TestBackfillFromBank:
    """启动回填：把功能上线前就存在的历史标签补进记忆。

    硬约束三条：只注入缺失项（幂等）、时间戳确定性（不用 now）、
    与真实 record_usage 共存时不能覆盖真实计数。
    """

    SEED = [("选修1教材103T19", 1), ("选修1教材P58习题7", 2), ("教材P98T4", 1)]

    def test_injects_only_missing_tags(self):
        assert tag_memory.backfill_from_bank("custom", self.SEED) == 3
        tags = {item["tag"] for item in tag_memory.list_memory("custom")}
        assert tags == {"选修1教材103T19", "选修1教材P58习题7", "教材P98T4"}
        counts = {item["tag"]: item["use_count"] for item in tag_memory.list_memory("custom")}
        assert counts["选修1教材P58习题7"] == 2

    def test_second_run_adds_nothing_and_keeps_entries_unchanged(self):
        assert tag_memory.backfill_from_bank("custom", self.SEED) == 3
        before = {item["tag"]: dict(item) for item in tag_memory.list_memory("custom")}
        # 第二次传入完全相同的（tag, count）——包括故意改大 count，也必须不动。
        assert tag_memory.backfill_from_bank("custom", [(tag, 99) for tag, _ in self.SEED]) == 0
        after = {item["tag"]: dict(item) for item in tag_memory.list_memory("custom")}
        assert after == before

    def test_partial_overlap_only_injects_the_new_one(self):
        tag_memory.record_usage("custom", ["教材P98T4"])
        before = tag_memory.list_memory("custom")[0]
        assert tag_memory.backfill_from_bank("custom", [("教材P98T4", 7), ("教材p93例3", 1)]) == 1
        items = {item["tag"]: item for item in tag_memory.list_memory("custom")}
        # 已有记忆：真实使用次数 / 时间戳原样保留，绝不被回填覆盖。
        assert items["教材P98T4"]["use_count"] == before["use_count"]
        assert items["教材P98T4"]["last_used_at"] == before["last_used_at"]
        assert items["教材p93例3"]["use_count"] == 1

    def test_timestamps_are_deterministic(self, isolated_memory):
        tag_memory.backfill_from_bank("custom", self.SEED)
        first_bytes = isolated_memory.read_text(encoding="utf-8")
        stamps = [item["last_used_at"] for item in tag_memory.list_memory("custom")]
        assert stamps
        # 固定基准时间：不含当下时间，因此重跑出来的文件字节级一致。
        assert all(stamp.startswith("2026-01-01T00:00:00.0000") for stamp in stamps)
        assert len(set(stamps)) == len(stamps)
        tag_memory.clear_memory("custom")
        tag_memory.backfill_from_bank("custom", self.SEED)
        assert isolated_memory.read_text(encoding="utf-8") == first_bytes

    def test_real_save_after_backfill_increments_and_bumps(self):
        tag_memory.backfill_from_bank("custom", [("教材P98T4", 4)])
        before = tag_memory.list_memory("custom")[0]
        assert before["use_count"] == 4
        tag_memory.record_usage("custom", ["教材P98T4"])
        after = tag_memory.list_memory("custom")
        assert len(after) == 1
        assert after[0]["use_count"] == 5
        assert after[0]["last_used_at"] != before["last_used_at"]

    def test_trims_to_max_entries_per_dim(self, monkeypatch):
        monkeypatch.setattr(tag_memory, "MAX_MEMORY_ENTRIES_PER_DIM", 5)
        entries = [(f"tag-{index}", 1) for index in range(12)]
        assert tag_memory.backfill_from_bank("custom", entries) == 12
        assert len(tag_memory.list_memory("custom")) == 5

    def test_unknown_dim_and_empty_input_are_noop(self):
        assert tag_memory.backfill_from_bank("bogus", self.SEED) == 0
        assert tag_memory.backfill_from_bank("custom", []) == 0
        assert tag_memory.backfill_from_bank("custom", [("   ", 3), ("", 1)]) == 0


class TestHarvestFromBank:
    """只读捞出题库历史取值（绝不写库），并按题去重计数。"""

    def test_counts_each_question_once_across_both_sources(self, db_session):
        first = Question(content="题干1", tags="教材P98T4,选修1教材P54例1")
        second = Question(content="题干2", tags="教材P98T4")
        db_session.add_all([first, second])
        db_session.commit()
        # 同一道题的新标签体系行 + legacy 字符串同时命中「教材P98T4」：只算一次。
        db_session.add(QuestionTag(question_id=first.id, dim="custom", code="教材P98T4"))
        db_session.add(QuestionTag(question_id=second.id, dim="custom", code="选修1教材P63例4改编"))
        db_session.add(QuestionTag(question_id=first.id, dim="micro", code="同构法·乘除同构"))
        db_session.add(QuestionTag(question_id=first.id, dim="chapter", code="不应被回填"))
        db_session.commit()

        harvested = main._harvest_free_text_tag_memory(db_session)
        custom = dict(harvested["custom"])
        assert custom == {"教材P98T4": 2, "选修1教材P54例1": 1, "选修1教材P63例4改编": 1}
        assert dict(harvested["micro"]) == {"同构法·乘除同构": 1}

    def test_harvest_does_not_write_the_database(self, db_session):
        question = Question(content="题干", tags="老标签")
        db_session.add(question)
        db_session.commit()
        before = db_session.query(QuestionTag).count()
        main._harvest_free_text_tag_memory(db_session)
        db_session.expire_all()
        assert db_session.query(QuestionTag).count() == before
        assert db_session.query(Question).count() == 1

    def test_backfill_writes_memory_from_harvest(self, db_session):
        question = Question(content="题干", tags="教材P98T4")
        db_session.add(question)
        db_session.commit()
        db_session.add(QuestionTag(question_id=question.id, dim="custom", code="选修1教材103T19"))
        db_session.commit()

        harvested = main._harvest_free_text_tag_memory(db_session)
        added = sum(
            tag_memory.backfill_from_bank(dim, entries)
            for dim, entries in harvested.items()
        )
        assert added == 2
        assert {item["tag"] for item in tag_memory.list_memory("custom")} == {
            "教材P98T4",
            "选修1教材103T19",
        }

    def test_backfill_failure_never_raises(self, monkeypatch):
        def boom(_dim, _entries):
            raise OSError("记忆文件只读")

        monkeypatch.setattr(tag_memory, "backfill_from_bank", boom)
        monkeypatch.setattr(main, "_harvest_free_text_tag_memory", lambda _db: {"custom": [("x", 1)]})
        assert main._backfill_free_text_tag_memory() is None

    def test_harvest_failure_is_swallowed(self, monkeypatch):
        def boom(_db):
            raise RuntimeError("题库不可读")

        monkeypatch.setattr(main, "_harvest_free_text_tag_memory", boom)
        assert main._backfill_free_text_tag_memory() is None

    def test_lifespan_runs_backfill_before_serving(self, monkeypatch):
        """启动钩子里必须调用回填（IS_TESTING 时 lifespan 直接 yield，故显式关掉）。"""

        import asyncio

        calls = []

        class FakeTasks:
            def start_maintenance(self, *, interval_seconds):
                calls.append(("maintenance", interval_seconds))

            def shutdown(self, *, wait):
                calls.append(("shutdown", wait))

        class FakeThread:
            def __init__(self, *, target, name, daemon):
                calls.append(("thread", target.__name__))

            def start(self):
                calls.append(("thread_started",))

        monkeypatch.setattr(main, "IS_TESTING", False)
        monkeypatch.setattr(main, "DOCUMENT_TASKS", FakeTasks())
        monkeypatch.setattr(main.threading, "Thread", FakeThread)
        monkeypatch.setattr(main, "_backfill_free_text_tag_memory", lambda: calls.append("backfill"))

        async def exercise():
            async with main.app_lifespan(main.app):
                pass

        asyncio.run(exercise())
        assert calls[0] == "backfill"


class TestMemoryAPI:
    def test_list_use_delete_roundtrip(self, client):
        resp = client.post(
            "/api/tag-memory/use",
            data={"dim": "custom", "tags": "期中,真题"},
            headers=AUTH,
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["status"] == "success"

        listing = client.get("/api/tag-memory", params={"dim": "custom"}).json()
        tags = [item["tag"] for item in listing["items"]]
        assert set(tags) == {"期中", "真题"}

        removed = client.delete("/api/tag-memory", params={"dim": "custom", "tag": "期中"}, headers=AUTH)
        assert removed.status_code == 200
        tags = [item["tag"] for item in client.get("/api/tag-memory", params={"dim": "custom"}).json()["items"]]
        assert tags == ["真题"]

        cleared = client.delete("/api/tag-memory", params={"dim": "custom", "clear": "true"}, headers=AUTH)
        assert cleared.status_code == 200
        assert client.get("/api/tag-memory", params={"dim": "custom"}).json()["items"] == []

    def test_rejects_unknown_dim(self, client):
        assert client.get("/api/tag-memory", params={"dim": "bogus"}).json()["items"] == []
        assert client.delete("/api/tag-memory", params={"dim": "bogus", "tag": "x"}, headers=AUTH).status_code == 404


class TestMicroVocabEndpoint:
    def test_vocab_shape(self, client):
        payload = client.get("/api/config/micro-method-vocab").json()
        assert payload["version"] == "micro-method-v1"
        assert len(payload["modules"]) == 8
        first = payload["modules"][0]
        assert first["name"] == "函数与导数"
        assert any(f["name"] == "同构法" for f in first["families"])


class TestDeprecatedUtcnowIsNotUsed:
    """datetime.utcnow() 自 Python 3.12 起已弃用，且返回的是 naive UTC。

    便携包自带的是 Python 3.10，但升级到 3.12+ 后每个 DeprecationWarning
    都会在控制台刷屏，最终版本里还会直接移除。这里锁死「不再使用它」。
    """

    def test_module_source_has_no_utcnow(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(tag_memory))
        # 只看真实调用，注释/文档字符串里提到 utcnow 不算。
        calls = [
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        ]
        assert "utcnow" not in calls, "datetime.utcnow() 已弃用，请改用 _utcnow_naive()"

    def test_record_usage_still_writes_naive_utc(self, isolated_memory):
        entries = tag_memory.record_usage("custom", ["弃用检查"])
        assert entries
        stamp = entries[0]["last_used_at"]
        # 与 database._utcnow_naive 同口径：naive、UTC、可解析。
        from datetime import datetime, timezone

        parsed = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%fZ")
        assert parsed.tzinfo is None
        delta = abs((datetime.now(timezone.utc).replace(tzinfo=None) - parsed).total_seconds())
        assert delta < 60, "时间戳必须是「当下」的 UTC，不能是本地时间或固定基准"
