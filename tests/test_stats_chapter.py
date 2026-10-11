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


def test_stats_function_tag_counts_folds_case_variants(client, db_session):
    """大小写不同的功能标签在统计出参里合并成一个键。

    库里保留历史脏数据原样（写入不归并），只有 `/api/stats` 的
    `function_tag_counts` 做忽略大小写的合并，且采用词表写法作为键名。
    """
    from mathbank.database import Question, QuestionTag

    questions = []
    for index, content in enumerate(["统计测试-功能标签甲", "统计测试-功能标签乙"]):
        question = Question(
            content=content,
            question_type="detailed_answer",
            difficulty="medium",
        )
        db_session.add(question)
        questions.append(question)
    db_session.commit()

    # 同一个语义的两种大小写：typical（词表写法）与 Typical（历史脏数据）
    db_session.add(
        QuestionTag(question_id=questions[0].id, dim="function", code="typical")
    )
    db_session.add(
        QuestionTag(question_id=questions[1].id, dim="function", code="Typical")
    )
    db_session.commit()

    counts = client.get("/api/stats").json()["function_tag_counts"]
    # 词表取值始终出现
    assert set(counts) >= {"error_prone", "innovative", "typical"}
    assert counts["typical"] == 2
    assert sum(counts.values()) == 2
    # 脏写法不再单独占一个键
    assert "Typical" not in counts

    # 库里的原始取值没有被改写
    stored = {
        row[0]
        for row in db_session.query(QuestionTag.code)
        .filter(QuestionTag.dim == "function")
        .all()
    }
    assert stored == {"typical", "Typical"}


def test_merge_case_insensitive_counts_prefers_vocabulary():
    """归并只在读取侧发生：词表写法优先，其余保留先出现者。"""
    from main import _merge_case_insensitive_counts

    merged = _merge_case_insensitive_counts(
        {"Typical": 3, "typical": 1, "error_prone": 0},
        preferred={"error_prone", "innovative", "typical"},
    )
    assert merged == {"typical": 4, "error_prone": 0}


def test_stats_book_catalog_lists_every_stage(client):
    """全册目录：即使只有一册有题，book_catalog 也必须列出全部册次。

    统计面板的学段下拉以 book_catalog 为数据源，0 题册次计数为 0 但仍可见
    可选，修掉"下拉里只有选择性必修第一册"的问题。
    """
    _create(client, "统计测试-目录专用题", ["X1-C2-S1"])

    stats = client.get("/api/stats").json()
    catalog = stats["book_catalog"]
    assert [b["book_code"] for b in catalog] == ["B1", "B2", "X1", "X2", "X3"]

    by_code = {b["book_name"]: b for b in catalog}
    assert by_code["选择性必修第一册"]["count"] == 1
    assert by_code["必修第一册"]["count"] == 0
    assert by_code["选择性必修第三册"]["count"] == 0

    # 每册都带完整章列表（0 题章也在），供章节下拉与册级汇总使用
    b1 = by_code["必修第一册"]
    assert len(b1["chapters"]) == 5
    assert all(ch["count"] == 0 for ch in b1["chapters"])

    c2 = next(
        ch
        for ch in by_code["选择性必修第一册"]["chapters"]
        if ch["chapter_code"] == "X1-C2"
    )
    assert c2["count"] == 1
    assert c2["chapter_name"] == "第2章 直线和圆的方程"
