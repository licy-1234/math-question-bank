"""新标签体系（解题策略 / 微观方法）的写入、回显、筛选与 AI 融合验证。"""

import pytest

from main import LOCAL_TOKEN
from mathbank.classify_rules import classify_with_rules
from mathbank.tags import allowed_codes, dimension_values

AUTH = {"X-Local-Token": LOCAL_TOKEN}


class TestStrategySchema:
    def test_strategy_vocabulary_matches_standard(self):
        values = dimension_values("strategy")
        labels = [v["label"] for v in values]
        assert labels == [
            "换元法", "整体代换法", "待定系数法", "配方法", "构造法", "特殊值法",
            "放缩法", "间接法", "算两次", "主元法", "对称性利用", "反证法与同一法",
            "数学归纳法",
        ]
        assert set(allowed_codes("strategy")) == {f"S{i:02d}" for i in range(1, 14)}

    def test_thought_order_matches_standard(self):
        labels = [v["label"] for v in dimension_values("thought")]
        assert labels[0] == "转化与化归思想"
        assert labels[1] == "函数与方程思想"
        assert labels[-1] == "符号化思想"
        # code 集合不变（存量数据零迁移）
        assert len(labels) == 15


class TestSaveAndEcho:
    def test_create_question_with_strategy_and_micro(self, client):
        resp = client.post(
            "/api/questions",
            data={
                "content": "测试题：求 $f(x)=x^2$ 在 R 上的最小值。",
                "question_type": "fill_in_blank",
                "difficulty": "easy",
                "tag_strategy_codes": '["S04", "S01"]',
                "tag_micro_methods": '["配方法", "配方法", "  基本不等式·乘1法  "]',
                "tag_custom_tags": "测试标签",
            },
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        payload = resp.json()
        question = payload["question"]
        tag_codes = question["tag_codes"]
        # 落库后按 dim 主键序 = 用户输入顺序（保留老师勾选顺序）
        assert tag_codes["strategy"] == ["S04", "S01"]
        assert sorted(tag_codes["micro"]) == ["基本不等式·乘1法", "配方法"]

        detail = client.get(f"/api/questions/{question['id']}").json()
        echo = detail.get("tag_codes") or detail
        assert set(echo["strategy"]) == {"S01", "S04"}
        assert set(echo["micro"]) == {"基本不等式·乘1法", "配方法"}

        # 保存同时写入记忆
        memory = client.get("/api/tag-memory", params={"dim": "micro"}).json()
        memory_tags = {item["tag"] for item in memory["items"]}
        assert {"配方法", "基本不等式·乘1法"} <= memory_tags

        # 清理测试题
        client.delete(f"/api/questions/{question['id']}")

    def test_strategy_rejects_unknown_codes(self, client):
        resp = client.post(
            "/api/questions",
            data={
                "content": "测试题：白名单校验。",
                "question_type": "fill_in_blank",
                "difficulty": "easy",
                "tag_strategy_codes": '["S99", "S02"]',
            },
            headers=AUTH,
        )
        assert resp.status_code == 200
        tag_codes = resp.json()["question"]["tag_codes"]
        assert tag_codes["strategy"] == ["S02"]


class TestFiltering:
    @pytest.fixture()
    def seeded(self, client):
        created = []
        specs = [
            {"content": "策略筛选甲：配方求最值。", "tag_strategy_codes": '["S04"]',
             "tag_micro_methods": '["配方法"]'},
            {"content": "策略筛选乙：换元处理根式。", "tag_strategy_codes": '["S01"]',
             "tag_micro_methods": '["换元法"]'},
        ]
        for spec in specs:
            resp = client.post("/api/questions", data={
                "content": spec["content"],
                "question_type": "fill_in_blank",
                "difficulty": "easy",
                "tag_strategy_codes": spec["tag_strategy_codes"],
                "tag_micro_methods": spec["tag_micro_methods"],
            }, headers=AUTH)

            assert resp.status_code == 200
            created.append(resp.json()["question"]["id"])
        yield created
        for qid in created:
            client.delete(f"/api/questions/{qid}")

    def test_filter_by_strategy(self, client, seeded):
        resp = client.get("/api/questions", params={"strategy": "S04"})
        contents = [q["content"] for q in resp.json()]
        assert any("策略筛选甲" in c for c in contents)
        assert not any("策略筛选乙" in c for c in contents)

    def test_filter_by_micro(self, client, seeded):
        resp = client.get("/api/questions", params={"micro_method": "换元法"})
        contents = [q["content"] for q in resp.json()]
        assert any("策略筛选乙" in c for c in contents)
        assert not any("策略筛选甲" in c for c in contents)

    def test_tag_values_endpoint_lists_micro(self, client, seeded):
        payload = client.get("/api/tag-values", params={"dim": "micro"}).json()
        values = {item["value"] for item in payload["items"]}
        assert {"配方法", "换元法"} <= values


class TestAIFusion:
    def test_classify_fuses_tag_suggestions_with_whitelist(self):
        result = classify_with_rules(
            "已知函数 f(x)=x^3-3x，讨论其单调性并求极值。",
            {
                "question_type": "detailed_answer",
                "difficulty": "medium",
                "chapter_code": "X2-C3",
                "function_tag": "typical",
                "thought_codes": ["T01", "T999"],
                "strategy_codes": ["S05", "Sxx"],
                "micro_methods": ["  求导法 ", "求导法", "极值计算"],
            },
        )
        assert result["function_tag"] == "typical"
        assert result["thought_codes"] == ["T01"]
        assert result["strategy_codes"] == ["S05"]
        assert result["micro_methods"] == ["求导法", "极值计算"]

    def test_classify_empty_suggestions(self):
        result = classify_with_rules("题目内容", {})
        assert result["function_tag"] == ""
        assert result["thought_codes"] == []
        assert result["strategy_codes"] == []
        assert result["micro_methods"] == []


class TestRelationTypes:
    def test_builtin_relation_types_reduced(self, client):
        payload = client.get("/api/relation-types").json()
        builtin = [item for item in payload["items"] if item["builtin"]]
        labels = [item["label"] for item in builtin]
        assert labels == ["多题归一", "一题多变"]

    def test_legacy_relation_code_still_accepted(self, client):
        from main import validate_relation_code
        assert validate_relation_code("multi_q_unify") == "multi_q_unify"
        assert validate_relation_code("one_q_variants") == "one_q_variants"
        assert validate_relation_code("same_topic") == "same_topic"
        assert validate_relation_code("custom:一题多解对比") == "custom:一题多解对比"
        with pytest.raises(Exception):
            validate_relation_code("")
