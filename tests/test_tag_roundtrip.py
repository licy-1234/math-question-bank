"""多值标签（question_tags）写入 / 读取 / 编辑的往返契约测试。

这些用例专门盯住一个真实的数据丢失缺陷：
``sync_question_tags`` 的 ``legacy`` 参数是"册/章/节"三元组，而这三个表单字段
默认值是 ``""`` 而不是 ``None``，所以 ``legacy is not None`` 恒为真。修复前，
任何**不带** ``tag_chapter_codes`` 的保存都会把该题已有的章节标签清空或压成单值。

对应修复：``sync_question_tags`` 增加 ``legacy_prev``，只有"三元组确实带信息"
**且**"这次真的改动了"才由旧字段派生章节，否则视该维度为未提交、原样保留。
"""

import json

from main import LOCAL_TOKEN

from mathbank.tags import (
    allowed_codes,
    dimension_values,
    load_tag_schema,
    normalize_tag_codes,
)

HEADERS = {"X-Local-Token": LOCAL_TOKEN}

LEGACY_TRIPLE = {
    "category_compulsory": "选修一",
    "category_chapter": "2. 直线和圆的方程",
    "category_knowledge": "2.1 直线的倾斜角与斜率",
}
# 上面这个三元组经 resolve_legacy_code 解析出的章节码
LEGACY_CHAPTER_CODE = "X1-C2-S1"

MULTI_CHAPTER = ["X1-C2-S1", "X1-C2-S4", "B1-C1-S2"]


def _new_question(client, content, **overrides):
    payload = {
        "content": content,
        "question_type": "detailed_answer",
        "difficulty": "medium",
        "source": "标签往返测试",
        "answer_markdown": "答案",
        "tags": "",
        "image_paths": "[]",
    }
    payload.update(overrides)
    response = client.post("/api/questions", data=payload, headers=HEADERS)
    assert response.status_code == 200, response.text
    body = response.json()
    question_id = (body.get("question") or {}).get("id") or body.get("id")
    assert question_id, f"创建失败，返回体：{body}"
    return question_id


def _read(client, question_id):
    response = client.get(f"/api/questions/{question_id}")
    assert response.status_code == 200, response.text
    return response.json()


def _create_tagged_question(client, content):
    """建一道带 3 章节 + 2 思想方法 + 1 功能 + 2 自定义的题。"""
    return _new_question(
        client,
        content,
        tag_chapter_codes=json.dumps(MULTI_CHAPTER, ensure_ascii=False),
        tag_thought_codes=json.dumps(["T01", "T02"], ensure_ascii=False),
        tag_function_code="error_prone",
        tag_custom_tags="月考,压轴改编",
        **LEGACY_TRIPLE,
    )


def test_create_derives_chapter_from_legacy_triple(client):
    """语义 1：新建题不认识 tag_chapter_codes 时，仍要能从"册/章/节"派生章节。"""
    qid = _new_question(client, "由旧分类字段派生章节的题目", **LEGACY_TRIPLE)
    tags = _read(client, qid)["tag_codes"]
    assert tags["chapter"] == [LEGACY_CHAPTER_CODE]


def test_update_keeps_chapter_when_category_triple_unchanged(client):
    """语义 2：不带 tag_chapter_codes 且"册/章/节"没变 → 多值章节原样保留。"""
    qid = _create_tagged_question(client, "章节不变的编辑场景")
    assert _read(client, qid)["tag_codes"]["chapter"] == MULTI_CHAPTER

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "章节不变的编辑场景（已改正文）",
            "question_type": "detailed_answer",
            "difficulty": "hard",
            **LEGACY_TRIPLE,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["chapter"] == MULTI_CHAPTER, "章节被改动了，说明旧字段回退仍在误伤"
    assert tags["thought"] == ["T01", "T02"]
    assert tags["function"] == ["error_prone"]
    assert _read(client, qid)["difficulty"] == "hard"


def test_update_keeps_chapter_when_category_triple_is_empty(client):
    """语义 3：不带 tag_chapter_codes 且"册/章/节"是空的 → 章节原样保留。"""
    qid = _create_tagged_question(client, "空三元组编辑场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "空三元组编辑场景（已改正文）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "category_compulsory": "",
            "category_chapter": "",
            "category_knowledge": "",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["chapter"] == MULTI_CHAPTER, "空三元组把章节标签清空了"
    assert tags["thought"] == ["T01", "T02"]


def test_update_without_any_category_field_keeps_chapter(client):
    """语义 3b：完全不提交 category_* 表单项时，章节同样不能丢。"""
    qid = _create_tagged_question(client, "不提交旧分类字段的场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "不提交旧分类字段的场景（已改正文）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert _read(client, qid)["tag_codes"]["chapter"] == MULTI_CHAPTER


def test_explicit_empty_chapter_list_clears_chapter(client):
    """语义 4：明确提交空数组 "[]" 表示用户主动清空，此时允许清空。

    注意不能提交空字符串 "" —— 实测 FastAPI 会把空的 Optional 表单值合并成
    None，和"未提交"无法区分。前端 static/js/tags.js 的 getSelection 始终
    序列化成 JSON 数组，所以真实清空信号就是 "[]"。
    """
    qid = _create_tagged_question(client, "主动清空章节的场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "主动清空章节的场景（已清空章节）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "tag_chapter_codes": "[]",
            **LEGACY_TRIPLE,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["chapter"] == []
    # 其它维度不能被牵连
    assert tags["thought"] == ["T01", "T02"]
    assert tags["function"] == ["error_prone"]


def test_empty_string_chapter_field_is_treated_as_not_submitted(client):
    """空的 tag_chapter_codes 表单值等价于"没提交"，章节必须原样保留。"""
    qid = _create_tagged_question(client, "空字符串章节字段场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "空字符串章节字段场景（已改正文）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "tag_chapter_codes": "",
            **LEGACY_TRIPLE,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    assert _read(client, qid)["tag_codes"]["chapter"] == MULTI_CHAPTER


def test_changed_category_triple_rewrites_chapter(client):
    """语义 5：不带 tag_chapter_codes 但"册/章/节"确实改了 → 按新三元组派生覆盖。"""
    qid = _create_tagged_question(client, "旧分类字段被改动的场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "旧分类字段被改动的场景（改了册章节）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "category_compulsory": "必修一",
            "category_chapter": "1. 集合与常用逻辑用语",
            "category_knowledge": "1.1 集合的概念",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["chapter"], "改了册/章/节却没有派生出章节标签"
    assert tags["chapter"][0].startswith("B1"), f"应落到必修一，实际 {tags['chapter']}"


def test_partial_tag_update_does_not_drop_other_dimensions(client):
    """补一条：只提交一个维度的标签，其余维度必须原样保留。"""
    qid = _create_tagged_question(client, "只改思想方法的场景")

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "只改思想方法的场景",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "tag_thought_codes": json.dumps(["T07", "T08"], ensure_ascii=False),
            **LEGACY_TRIPLE,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["thought"] == ["T07", "T08"]
    assert tags["chapter"] == MULTI_CHAPTER
    assert tags["function"] == ["error_prone"]
    assert set(tags["custom"]) == {"月考", "压轴改编"}


def test_tag_codes_preserve_entry_order(client):
    """补一条：返回顺序应等于录入顺序，不能被索引的字典序悄悄重排。"""
    qid = _create_tagged_question(client, "标签顺序场景")
    assert _read(client, qid)["tag_codes"]["chapter"] == MULTI_CHAPTER


# ---------------------------------------------------------------------------
# 新增词表取值「典型题 / typical」的覆盖
# ---------------------------------------------------------------------------
#
# 功能标签是受控词表驱动的：词表在 mathbank/resources/tag_schema.json 里
# dim='function' 那一项。词表新增 typical 后，校验（normalize_tag_codes）、
# 详情读回、列表筛选、统计口径都必须自动跟上，这里逐个盯住。


def _schema_function_codes():
    """直接读 JSON，拿到词表里 function 维度的原始取值顺序。"""
    for dimension in load_tag_schema().get("dimensions", []):
        if dimension.get("key") == "function_tag":
            return [item["code"] for item in dimension.get("values", [])]
    return []


def test_typical_is_in_the_controlled_vocabulary():
    """词表层面：typical 必须已经是合法取值，且校验能原样通过。"""
    assert "typical" in allowed_codes("function")
    assert "typical" in allowed_codes("function_tag")
    assert normalize_tag_codes("function", ["typical"]) == ["typical"]


def test_dimension_values_returns_controlled_vocabulary_in_schema_order():
    """dimension_values 必须保留 JSON 的原始顺序（用于下拉/统计的展示顺序）。"""
    expected = _schema_function_codes()
    assert expected == ["error_prone", "innovative", "typical"]

    values = dimension_values("function")
    assert values == [
        {"code": "error_prone", "label": "易错题"},
        {"code": "innovative", "label": "创新题"},
        {"code": "typical", "label": "典型题"},
    ]
    # schema key 与存储短名等价；返回值是新建 dict，改动不污染 lru_cache 的词表
    assert [item["code"] for item in values] == expected
    assert dimension_values("function_tag") == values
    assert [item["code"] for item in dimension_values("function_tag")] == expected
    values[0]["label"] = "被改写"
    assert dimension_values("function")[0]["label"] == "易错题"

    # 未知维度返回空列表而不是抛异常
    assert dimension_values("no_such_dim") == []


def test_create_question_with_typical_function_tag_roundtrips(client):
    """全链路：新建时 tag_function_code=typical，详情与列表都要读得回来。"""
    qid = _new_question(client, "典型题往返测试", tag_function_code="typical")

    detail = _read(client, qid)
    assert detail["tag_codes"]["function"] == ["typical"]

    listed = client.get("/api/questions").json()
    matched = [item for item in listed if item["id"] == qid]
    assert matched, f"新题没出现在列表里：{[item['id'] for item in listed]}"
    assert matched[0]["tag_codes"]["function"] == ["typical"]


def test_update_question_can_switch_to_typical_function_tag(client):
    """编辑场景：把一道 error_prone 的题改成 typical，读回来只剩 typical。"""
    qid = _create_tagged_question(client, "改成典型题的场景")
    assert _read(client, qid)["tag_codes"]["function"] == ["error_prone"]

    response = client.put(
        f"/api/questions/{qid}",
        data={
            "content": "改成典型题的场景（已改标签）",
            "question_type": "detailed_answer",
            "difficulty": "medium",
            "tag_function_code": "typical",
            **LEGACY_TRIPLE,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text

    tags = _read(client, qid)["tag_codes"]
    assert tags["function"] == ["typical"], "功能标签被旧的 error_prone 覆盖了"
    # 功能标签的切换不能牵连其它维度
    assert tags["thought"] == ["T01", "T02"]
    assert tags["chapter"] == MULTI_CHAPTER


def test_list_endpoint_filters_by_typical_function_code(client):
    """列表筛选：function_code=typical 只返回典型题，不能混入易错题。"""
    typical_a = _new_question(client, "列表筛选-典型题A", tag_function_code="typical")
    typical_b = _new_question(client, "列表筛选-典型题B", tag_function_code="typical")
    error_prone_id = _new_question(
        client, "列表筛选-易错题", tag_function_code="error_prone"
    )
    _new_question(client, "列表筛选-无功能标签")

    rows = client.get("/api/questions", params={"function_code": "typical"}).json()
    ids = [row["id"] for row in rows]
    assert set(ids) == {typical_a, typical_b}, f"典型题筛选结果不符：{ids}"
    assert error_prone_id not in ids
    assert all(row["tag_codes"]["function"] == ["typical"] for row in rows)


def test_stats_reports_function_tag_distribution(client):
    """/api/stats 的 function_tag_counts：词表取值全出现（含 0 值），并与库对齐。"""
    _new_question(client, "统计-易错题", tag_function_code="error_prone")
    _new_question(client, "统计-易错题2", tag_function_code="error_prone")
    _new_question(client, "统计-创新题", tag_function_code="innovative")
    _new_question(client, "统计-无功能标签")

    body = client.get("/api/stats").json()
    assert body["status"] == "success", body

    counts = body["function_tag_counts"]
    # 新加入词表、库里尚无题目的 typical 也要补 0 出现
    assert counts == {"error_prone": 2, "innovative": 1, "typical": 0}


def test_stats_function_tag_counts_includes_typical_questions(client):
    """统计口径要跟上 typical：建了典型题之后计数必须真实反映。"""
    _new_question(client, "统计-典型题", tag_function_code="typical")

    counts = client.get("/api/stats").json()["function_tag_counts"]
    assert counts["typical"] == 1
    assert counts["error_prone"] == 0
    assert counts["innovative"] == 0


def test_stats_function_tag_counts_keeps_unknown_legacy_codes(client, db_session):
    """question_tags.code 没有 CHECK 约束，历史脏 code 不能被统计丢掉。"""
    from mathbank.database import Question, QuestionTag

    question = Question(
        content="统计-历史脏数据",
        question_type="detailed_answer",
        difficulty="medium",
    )
    db_session.add(question)
    db_session.flush()
    db_session.add(
        QuestionTag(question_id=question.id, dim="function", code="legacy_flag")
    )
    db_session.commit()

    counts = client.get("/api/stats").json()["function_tag_counts"]
    assert counts["legacy_flag"] == 1
    assert counts["typical"] == 0
