"""统计接口章节数据的契约测试。

`/api/stats` 的 `compulsory_chapter_counts` / `chapter_stats` 必须从多值标签
系统（`question_tags`，dim='chapter'）读取，而不是旧的
`questions.category_compulsory/category_chapter` 字段。
"""

import json

from main import LOCAL_TOKEN

HEADERS = {"X-Local-Token": LOCAL_TOKEN}


def _create(client, content, chapter_codes, difficulty="medium"):
    resp = client.post(
        "/api/questions",
        data={
            "content": content,
            "question_type": "detailed_answer",
            "difficulty": difficulty,
            "tag_chapter_codes": json.dumps(chapter_codes, ensure_ascii=False),
            "image_paths": "[]",
        },
        headers=HEADERS,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_stats_chapter_reads_from_tags(client):
    _create(client, "统计测试-斜率定义", ["X1-C2-S1"])
    _create(client, "统计测试-斜率公式", ["X1-C2-S1"])
    _create(client, "统计测试-直线方程", ["X1-C2-S2"], difficulty="hard")

    stats = client.get("/api/stats").json()
    assert stats["status"] == "success"

    chapters = stats["chapter_stats"]
    assert len(chapters) == 1
    c = chapters[0]
    assert c["book_code"] == "X1"
    assert c["book_name"] == "选择性必修第一册"
    assert c["chapter_code"] == "X1-C2"
    assert c["chapter_name"] == "第2章 直线和圆的方程"
    assert c["count"] == 3

    sections = {s["code"]: s["count"] for s in c["sections"]}
    assert sections == {"X1-C2-S1": 2, "X1-C2-S2": 1}

    # 向后兼容的嵌套结构用展示名作为键
    assert "选择性必修第一册" in stats["compulsory_chapter_counts"]
    assert (
        stats["compulsory_chapter_counts"]["选择性必修第一册"]["第2章 直线和圆的方程"]
        == 3
    )


def test_stats_chapter_legacy_fallback(client, db_session):
    """没有章节标签（旧库未迁移）时，回退到旧字段，保证旧数据仍被统计。

    直接落库一条带旧字段、但无任何 question_tags 的题目，模拟迁移前的老数据。
    """
    from mathbank.database import Question

    db_session.add(
        Question(
            content="统计测试-旧数据",
            question_type="detailed_answer",
            difficulty="medium",
            category_compulsory="必修一",
            category_chapter="第一章",
            category_knowledge="",
        )
    )
    db_session.commit()

    stats = client.get("/api/stats").json()
    # 旧数据没有 chapter_stats 条目（无章节码），但会落在嵌套结构的兜底桶
    assert "必修一" in stats["compulsory_chapter_counts"]
    assert stats["compulsory_chapter_counts"]["必修一"]["第一章"] == 1
