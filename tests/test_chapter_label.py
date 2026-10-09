"""卡片「未分类」标签修复的单元测试：chapter_display_label 的解析规则。

题目卡片的文件夹标签过去只读旧字段 ``category_knowledge/category_chapter``，
标签迁移清空旧字段后几乎所有题目都误显示"未分类"。现在后端在
``to_summary_dict`` 里输出 ``chapter_label``，把多值章节标签解析成
"最深公共展示单元"的中文地名。
"""

from mathbank.tags import chapter_display_label


def test_single_section_uses_numbered_section_name():
    assert chapter_display_label(["X1-C2-S5"]) == "2.5 直线与圆、圆与圆的位置关系"


def test_subsections_collapse_into_parent_section():
    # 实库案例：一道题同时挂在 S2 的三个小节上，应展示所属"节"
    label = chapter_display_label(["X1-C2-S2-P1", "X1-C2-S2-P2", "X1-C2-S2-P3"])
    assert label == "2.2 直线的方程"


def test_chapter_level_tag_shows_chapter_title():
    assert chapter_display_label(["X1-C2"]) == "第2章 直线和圆的方程"


def test_book_level_tag_shows_book_name():
    assert chapter_display_label(["X1"]) == "选择性必修第一册"


def test_sections_of_same_chapter_collapse_to_chapter():
    label = chapter_display_label(["X1-C2-S1", "X1-C2-S5"])
    assert label == "第2章 直线和圆的方程"


def test_chapters_of_same_book_collapse_to_book():
    label = chapter_display_label(["X1-C1", "X1-C2"])
    assert label == "选择性必修第一册"


def test_empty_or_unknown_codes_return_empty_string():
    assert chapter_display_label([]) == ""
    assert chapter_display_label(["NOT-A-CODE"]) == ""
    assert chapter_display_label([None, "", "  "]) == ""
