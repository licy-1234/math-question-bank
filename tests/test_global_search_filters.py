"""顶部全局搜索 + 填空式筛选的参数穿透。

覆盖需求：搜索框要命中题干、解析、评注、来源、标签、方法（宏观/中观/微观，
含中文名 → 机器码）与关联；题库列表要支持 source / tag 两个新的填空式筛选。
"""

import pytest

from main import LOCAL_TOKEN

AUTH = {"X-Local-Token": LOCAL_TOKEN}

STEM = "全局搜索用例：已知函数 $f(x)$ 的最大值。"
ANSWER = "解：这里出现只在解析里才会写的关键词 ZIYOUanswer。"
REVIEW = "评注：这里出现只在评注里才会写的关键词 ZIYOUreview。"
SOURCE = "2026届高三一模"


def _create_question(client, **extra):
    data = {
        "content": STEM,
        "question_type": "detailed_answer",
        "difficulty": "medium",
        "answer_markdown": ANSWER,
        "review": REVIEW,
        "source": SOURCE,
        "tag_thought_codes": '["T02"]',
        "tag_strategy_codes": '["S01"]',
        "tag_micro_methods": '["同构法·乘除同构"]',
        "tag_custom_tags": "自定义KEY标签",
    }
    data.update(extra)
    resp = client.post("/api/questions", data=data, headers=AUTH)
    assert resp.status_code == 200, resp.text
    question = resp.json()["question"]
    yield question
    client.delete(f"/api/questions/{question['id']}", headers=AUTH)


@pytest.fixture()
def sample_question(client):
    yield from _create_question(client)


def _ids(client, **params):
    params.setdefault("page", 1)
    params.setdefault("page_size", 50)
    payload = client.get("/api/questions", params=params).json()
    return [item["id"] for item in payload["items"]]


class TestGlobalSearchDimensions:
    """搜索框必须横跨全部维度，而不是只扫题干。"""

    @pytest.mark.parametrize("keyword", [
        "全局搜索用例",        # 题干
        "ZIYOUanswer",        # 解析
        "ZIYOUreview",        # 评注
        "高三一模",            # 来源
        "自定义KEY标签",       # 自定义标签
        "数形结合",            # 宏观数学思想的中文名 → T02
        "换元",                # 中观解题策略的中文名 → S01
        "同构法",              # 微观解题方法（自由文本）
    ])
    def test_keyword_hits_every_dimension(self, client, sample_question, keyword):
        assert sample_question["id"] in _ids(client, q=keyword)

    def test_keyword_hits_relation(self, client, sample_question):
        resp = client.post(
            "/api/questions",
            data={
                "content": "用来做关联的另一道题的题干。",
                "question_type": "detailed_answer",
                "difficulty": "medium",
            },
            headers=AUTH,
        )
        assert resp.status_code == 200, resp.text
        other = resp.json()["question"]
        try:
            resp = client.post(
                f"/api/questions/{sample_question['id']}/relations",
                data={
                    "target_id": other["id"],
                    "relation_code": "multi_q_unify",
                    "relation_label": "同源于一道母题ZZTOKEN",
                },
                headers=AUTH,
            )
            assert resp.status_code in (200, 201), resp.text
            assert sample_question["id"] in _ids(client, q="ZZTOKEN")
            assert sample_question["id"] in _ids(client, q="multi_q_unify")
        finally:
            client.delete(f"/api/questions/{other['id']}", headers=AUTH)

    def test_unrelated_keyword_returns_nothing(self, client, sample_question):
        assert sample_question["id"] not in _ids(client, q="绝对不存在的关键词ZZZ")


class TestTextFilters:
    """微观方法已改为填空搜索，来源 / 标签两个新筛选要能落到 API 参数上。"""

    def test_source_and_tag_filters(self, client, sample_question):
        assert sample_question["id"] in _ids(client, source="高三一模")
        assert sample_question["id"] in _ids(client, tag="自定义")
        assert sample_question["id"] not in _ids(client, source="不存在的来源ZZZ")

    def test_micro_method_filter_is_fuzzy(self, client, sample_question):
        assert sample_question["id"] in _ids(client, micro_method="同构")
        assert sample_question["id"] not in _ids(client, micro_method="ZZZ")
