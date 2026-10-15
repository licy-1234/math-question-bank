"""Catalog labels keep their identity when the model drops the front code.

Production defect: ``20261015.docx`` yielded a *hybrid* Word plan whose metadata
groups required an exact catalog pair.  The model returned the human chapter
name without the curriculum's decorative front code ("2. 直线和圆的方程" came
back as "直线和圆的方程"), so every metadata group failed validation and 6 of 7
questions were silently dropped; the surviving question also reached the review
card with empty 学段/章节 selects.

The fix resolves the model's name back to its unique catalog label before the
strict membership check, and re-applies the same resolution to every final
question so the import card preselects instead of forcing manual tagging.
"""

from copy import deepcopy
import json
import uuid

import pytest

import main
from mathbank import docx_helper
from mathbank.curriculums import canonical_category_labels, curriculum_display_label
from mathbank.source_metadata import apply_source_metadata_response


CATALOG = {
    "必修一": {
        "1. 集合与常用逻辑用语": ["1.1 集合的概念", "1.2 集合间的基本关系"],
        "3. 函数的概念与性质": ["3.1 函数的概念及其表示", "3.2 函数的基本性质"],
    }
}


# --------------------------- pure resolution ---------------------------


def test_display_label_drops_only_the_front_catalog_code():
    assert curriculum_display_label("2. 直线和圆的方程") == "直线和圆的方程"
    assert curriculum_display_label("2.5 直线与圆、圆与圆的位置关系") == "直线与圆、圆与圆的位置关系"
    assert curriculum_display_label("10. 概率") == "概率"
    assert curriculum_display_label("第一章 集合与常用逻辑用语") == "集合与常用逻辑用语"
    assert curriculum_display_label("集合与常用逻辑用语") == "集合与常用逻辑用语"


def test_code_less_chapter_and_section_resolve_to_exact_catalog_keys():
    assert canonical_category_labels(CATALOG, "必修一", "函数的概念与性质") == (
        "必修一", "3. 函数的概念与性质", "")
    assert canonical_category_labels(CATALOG, "必修一", "3. 函数的概念与性质", "函数的基本性质") == (
        "必修一", "3. 函数的概念与性质", "3.2 函数的基本性质")
    # Exact input is returned unchanged (idempotent).
    assert canonical_category_labels(CATALOG, "必修一", "3. 函数的概念与性质",
                                     "3.1 函数的概念及其表示") == (
        "必修一", "3. 函数的概念与性质", "3.1 函数的概念及其表示")


def test_book_alias_and_chapter_wrapper_resolve_like_the_lead_probe():
    # 用户在册栏用的是教材习惯命名，目录用的是简化册名 + 序号章名。
    assert canonical_category_labels(CATALOG, "必修第一册", "第3章 函数的概念与性质") == (
        "必修一", "3. 函数的概念与性质", "")
    assert canonical_category_labels(CATALOG, "必修一", "函数的概念与性质") == (
        "必修一", "3. 函数的概念与性质", "")
    assert canonical_category_labels(CATALOG, "必修一", "第一章 集合与常用逻辑用语") == (
        "必修一", "1. 集合与常用逻辑用语", "")


def test_a_fabricated_section_downgrades_to_whole_chapter_instead_of_failing():
    # 「整章待细分」（空小节）是合法状态，绝不能因小节非法而丢掉整题。
    assert canonical_category_labels(CATALOG, "必修一", "3. 函数的概念与性质", "9.9 不存在的节") == (
        "必修一", "3. 函数的概念与性质", "")


def test_unknown_or_ambiguous_book_or_chapter_are_not_guessed():
    assert canonical_category_labels(CATALOG, "必修一", "未知章节") is None
    assert canonical_category_labels(CATALOG, "必修二", "函数的概念与性质") is None
    assert canonical_category_labels(CATALOG, "", "函数的概念与性质") is None
    ambiguous = {"必修一": {"3. 函数": [], "4. 函数": []}}
    assert canonical_category_labels(ambiguous, "必修一", "函数") is None


# --------------------------- metadata adoption ---------------------------


def test_metadata_adoption_accepts_a_code_less_chapter_and_stores_canonical_labels():
    from test_source_metadata import prepare, metadata

    plan = prepare("1. 已知变量$x=3$，求其值。\n2. 给定参数$y=4$，写出结果。")
    assert plan["eligible"], plan["fallback_reasons"]
    parsed = metadata(plan)
    for item in parsed["items"]:
        item["category_chapter"] = "函数的概念与性质"  # front code dropped
    questions = apply_source_metadata_response(parsed, plan, CATALOG, normalize_fillin=lambda value: value)
    assert [q["category_chapter"] for q in questions] == ["3. 函数的概念与性质"] * len(questions)
    assert [q["category_compulsory"] for q in questions] == ["必修一"] * len(questions)


def test_metadata_adoption_keeps_a_genuinely_unknown_chapter_with_blank_classes():
    from test_source_metadata import prepare, metadata

    plan = prepare("1. 已知变量$x=3$，求其值。")
    parsed = metadata(plan)
    parsed["items"][0]["category_chapter"] = "未知章节"
    questions = apply_source_metadata_response(parsed, plan, CATALOG, normalize_fillin=lambda value: value)
    # 分类彻底无法归位：保留题目、置空分类、给出分类待核标记，不丢题。
    assert len(questions) == 1
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert questions[0]["category_compulsory"] == "" and questions[0]["category_chapter"] == ""
    assert questions[0]["classification_review"]["required"] is True
    # 分类疑点绝不能伪装成「原文待核验」——否则会多触发一次付费的原文视觉核验。
    assert "source_review" not in questions[0]


@pytest.mark.parametrize("label,compulsory,chapter,knowledge,expected", [
    ("模型常见的教材命名", "选择性必修第一册", "第2章 直线和圆的方程", "",
     ("选修一", "2. 直线和圆的方程", "")),
    ("目录册名+去序号章名", "选修一", "直线和圆的方程", "", ("选修一", "2. 直线和圆的方程", "")),
    ("目录逐字命名", "选修一", "2. 直线和圆的方程", "", ("选修一", "2. 直线和圆的方程", "")),
    ("目录逐字 + 合法小节", "选修一", "2. 直线和圆的方程", "2.4 圆的方程",
     ("选修一", "2. 直线和圆的方程", "2.4 圆的方程")),
    ("目录逐字 + 非法小节", "选修一", "2. 直线和圆的方程", "2.9 不存在的节",
     ("选修一", "2. 直线和圆的方程", "")),
])
def test_lead_probe_cases_never_lose_a_question(label, compulsory, chapter, knowledge, expected):
    """复刻主理人 prove_category_validation.py 的 5 个用例作为回归断言。"""

    from test_source_metadata import prepare, metadata

    curriculum = {"选修一": {"1. 空间向量与立体几何": [], "2. 直线和圆的方程":
                             ["2.1 直线的倾斜角和斜率", "2.4 圆的方程", "2.5 直线与圆、圆与圆的位置关系"],
                             "3. 圆锥曲线的方程": []}}
    plan = prepare("1. 已知变量$x=3$，求其值。")
    parsed = metadata(plan)
    for item in parsed["items"]:
        item["category_compulsory"] = compulsory
        item["category_chapter"] = chapter
        item["category_knowledge"] = knowledge
    questions = apply_source_metadata_response(parsed, plan, curriculum, normalize_fillin=lambda value: value)
    assert len(questions) == len(plan["questions"]), label
    got = (questions[0]["category_compulsory"], questions[0]["category_chapter"],
           questions[0]["category_knowledge"])
    assert got == expected, label


def test_classification_fallback_keeps_the_body_with_a_blank_review_marker():
    from test_source_metadata import prepare, metadata

    plan = prepare("1. 已知变量$x=3$，求其值。")
    parsed = metadata(plan)
    parsed["items"][0]["category_chapter"] = "完全无关的章节名"
    questions = apply_source_metadata_response(
        parsed, plan, CATALOG, normalize_fillin=lambda value: value)
    assert len(questions) == 1
    assert questions[0]["category_compulsory"] == "" and questions[0]["category_chapter"] == ""
    assert questions[0]["content"] == plan["questions"][0]["content"]
    assert questions[0]["classification_review"]["required"] is True
    assert "source_review" not in questions[0]


# --------------------------- hybrid request path ---------------------------


def test_hybrid_metadata_group_survives_a_code_less_chapter(tmp_path):
    from test_word_hybrid_safety import native_plan, reply_for, Response, call

    plan = native_plan(tmp_path)
    assert plan["mode"] == "hybrid"
    metadata_groups = [g for g in plan["groups"] if g["route"] == "metadata"]
    assert metadata_groups, plan["groups"]

    def post(_provider, payload, **_options):
        parsed = reply_for(plan, payload)
        for item in parsed["metadata"]["items"]:
            item["category_chapter"] = "函数的概念与性质"  # every metadata id drops "3. "
        return Response(parsed)

    result, diagnostics = call(plan, post)
    assert not result.partial
    assert diagnostics["word_hybrid"]["status"] == "used"
    assert diagnostics["word_hybrid"]["failed_group_ids"] == []
    assert len(result.questions) == len(plan["_inspection"]["questions"])
    assert all(q["category_chapter"] == "3. 函数的概念与性质" for q in result.questions)


def test_hybrid_metadata_group_with_unresolvable_category_is_preserved_not_dropped(tmp_path):
    """分类彻底无法归位时，题目仍必须保留（置空分类 + 待核标记），不得整组消失。"""

    from test_word_hybrid_safety import native_plan, reply_for, Response, call

    plan = native_plan(tmp_path)
    total = len(plan["_inspection"]["questions"])
    metadata_ids = {qid for g in plan["groups"] if g["route"] == "metadata" for qid in g["question_ids"]}

    def post(_provider, payload, **_options):
        parsed = reply_for(plan, payload)
        for item in parsed["metadata"]["items"]:
            item["category_chapter"] = "完全无关的章节名"
        return Response(parsed)

    result, diagnostics = call(plan, post)
    assert not result.partial
    assert len(result.questions) == total
    degraded = [q for q in result.questions if q.get("classification_review", {}).get("required")]
    assert degraded, "metadata 组必须给出待人工分类标记"
    for q in result.questions:
        if q.get("category_chapter") == "":
            assert q["category_compulsory"] == ""
    assert diagnostics["word_hybrid"]["status"] == "used"
    assert diagnostics["word_hybrid"]["failed_group_ids"] == []


# --------------------------- final question normalization ---------------------------


def test_postprocess_canonicalizes_split_path_labels_for_the_review_card(monkeypatch):
    monkeypatch.setattr(main, "get_current_curriculum", lambda: CATALOG)
    questions = [{
        "content": "已知 $f(x)=x^2$。", "answer_markdown": "",
        "category_compulsory": "必修一", "category_chapter": "函数的概念与性质",
        "category_knowledge": None,
    }]
    result = main.post_process_pdf_parsed_questions(questions, "试题")
    assert result[0]["category_compulsory"] == "必修一"
    assert result[0]["category_chapter"] == "3. 函数的概念与性质"
    assert result[0]["category_knowledge"] == ""


def test_postprocess_downgrades_a_fabricated_section_to_whole_chapter(monkeypatch):
    monkeypatch.setattr(main, "get_current_curriculum", lambda: CATALOG)
    questions = [{
        "content": "已知 $f(x)$。", "answer_markdown": "",
        "category_compulsory": "必修一", "category_chapter": "函数的概念与性质",
        "category_knowledge": "9.9 不存在的节",
    }]
    result = main.post_process_pdf_parsed_questions(questions, "试题")
    assert result[0]["category_chapter"] == "3. 函数的概念与性质"
    assert result[0]["category_knowledge"] == ""


def test_postprocess_leaves_an_unresolvable_label_for_manual_review(monkeypatch):
    monkeypatch.setattr(main, "get_current_curriculum", lambda: CATALOG)
    questions = [{
        "content": "题目。", "answer_markdown": "",
        "category_compulsory": "必修一", "category_chapter": "未知章节",
    }]
    result = main.post_process_pdf_parsed_questions(questions, "试题")
    assert result[0]["category_chapter"] == "未知章节"


# --------------------------- real task end to end ---------------------------


class _Response:
    status_code = 200

    def __init__(self, parsed):
        self.parsed = parsed

    def json(self):
        return {"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.parsed, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}


def _run_docx_task_keeping_every_question(monkeypatch, tmp_path, code_less: bool):
    native = docx_helper._new_diagnostics()
    source = ("1. 已知集合 $A=\\{1,2\\}$，求其元素个数。\n\n"
              "2. 已知函数 $f(x)=x^2-2x$，求 $f(3)$ 的值。")
    monkeypatch.setattr(main, "extract_docx_markdown", lambda *_a, **_k: {
        "success": True, "markdown": source, "image_paths": [], "image_count": 0,
        "diagnostics": deepcopy(native)})
    monkeypatch.setattr(main, "TMP_UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(main, "get_current_curriculum", lambda: deepcopy(CATALOG))
    monkeypatch.setenv("PREFER_PARSE_MODEL", "DEEPSEEK/deepseek-flash")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "isolated-no-network")
    monkeypatch.setattr("requests.sessions.Session.request", lambda *_a, **_k: pytest.fail("Network forbidden"))

    category, chapters = next(iter(CATALOG.items()))
    chapter = next(iter(chapters))
    code_less_chapter = curriculum_display_label(chapter)
    assert code_less_chapter != chapter, "fixture needs a chapter carrying a front code"
    fields = {"question_type": "detailed_answer", "category_compulsory": category,
              "category_chapter": code_less_chapter if code_less else chapter,
              "category_knowledge": "", "difficulty": "hard"}

    def post(_provider, payload, **_kwargs):
        items = json.loads(payload["messages"][1]["content"])["items"]
        return _Response({"items": [{"id": item["id"], **fields} for item in items]})

    monkeypatch.setattr(main, "post_chat_completion", post)
    task_id = "code-less-label-" + uuid.uuid4().hex
    main.DOCUMENT_TASKS.create(task_id, document_type="docx", temp_assets=[])
    try:
        main.run_docx_parsing_task(task_id, b"isolated-test", "试题.docx", docx_verify_suspicions=False)
        return main.DOCUMENT_TASKS.snapshot(task_id), chapter
    finally:
        main.DOCUMENT_TASKS.remove(task_id)


def test_docx_task_keeps_both_questions_when_the_model_drops_the_front_code(monkeypatch, tmp_path):
    task, chapter = _run_docx_task_keeping_every_question(monkeypatch, tmp_path, code_less=True)
    assert task["status"] == "completed", task.get("error")
    assert len(task["data"]) == 2
    assert [q["category_chapter"] for q in task["data"]] == [chapter, chapter]


def test_docx_task_labels_setup_with_the_exact_code_still_works(monkeypatch, tmp_path):
    task, chapter = _run_docx_task_keeping_every_question(monkeypatch, tmp_path, code_less=False)
    assert task["status"] == "completed", task.get("error")
    assert len(task["data"]) == 2
    assert [q["category_chapter"] for q in task["data"]] == [chapter, chapter]
