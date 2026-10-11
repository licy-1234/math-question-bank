"""tag_memory 模块与 API 的行为验证：自定义标签 / 微观方法的记忆复用。"""

import pytest

from main import LOCAL_TOKEN
from mathbank import tag_memory

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
