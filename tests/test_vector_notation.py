"""Vector arrow notation must be standardized, idempotent and non-invasive."""

import pytest

from mathbank.vector_notation import normalize_vector_notation


@pytest.mark.parametrize(
    "source, expected",
    [
        # 多字符向量 → 整段拉伸箭头（中文教材写法）
        (r"$\vec{PB}$", r"$\overrightarrow{PB}$"),
        (r"$\vv{PB}$", r"$\overrightarrow{PB}$"),
        (r"$\overset{\rightarrow}{PB}$", r"$\overrightarrow{PB}$"),
        (r"$\overset{\to}{AB}$", r"$\overrightarrow{AB}$"),
        (r"$\overset{\longrightarrow}{AB}$", r"$\overrightarrow{AB}$"),
        # 花括号与命令之间的空格一并收拢（ID=9 的真实历史写法）
        (r"$\vec {BP}\cdot  \vec {PA}$", r"$\overrightarrow{BP}\cdot  \overrightarrow{PA}$"),
        (r"$\overrightarrow {AB}$", r"$\overrightarrow{AB}$"),
        (r"$\vec{ AB }$", r"$\overrightarrow{AB}$"),
        # 单字符向量保持紧凑重音
        (r"$\vec{a}$", r"$\vec{a}$"),
        (r"$\vec {a}$", r"$\vec{a}$"),
        (r"$\overset{\rightarrow}{a}$", r"$\vec{a}$"),
        (r"$\vec{v}_{1}$", r"$\vec{v}_{1}$"),
        # 反向箭头
        (r"$\overset{\longleftarrow}{BA}$", r"$\overleftarrow{BA}$"),
        (r"$\overset{\leftarrow}{b}$", r"$\overleftarrow{b}$"),
        # 混合出现时逐处改写，运算与间距不动
        (
            r"$\vec{v}=\vec{AB}=2\vec{CD}$",
            r"$\vec{v}=\overrightarrow{AB}=2\overrightarrow{CD}$",
        ),
        # OCR 常见的 Unicode 组合箭头
        ("A\u20d7 与 B\u20d7", r"\vec{A} 与 \vec{B}"),
        # 嵌套花括号不支持，原样保留交给 KaTeX
        (r"$\vec{v_{1}}$", r"$\vec{v_{1}}$"),
        # 非向量重音不受影响
        (r"$\overline{AB}$ 与 $\hat{AB}$", r"$\overline{AB}$ 与 $\hat{AB}$"),
    ],
)
def test_vector_notation_is_standardized(source, expected):
    assert normalize_vector_notation(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        r"$\vec{PB}$ 与 $\vec {BP}$ 与 $\overset{\to}{AB}$",
        "A\u20d7 与 $\vec{a}$",
        r"$\vec{v_{1}}$ + $\overrightarrow {AB}$",
        "",
    ],
)
def test_vector_notation_is_idempotent(source):
    once = normalize_vector_notation(source)
    assert normalize_vector_notation(once) == once


@pytest.mark.parametrize(
    "source",
    [
        # 行内代码 / 围栏代码里的 LaTeX 必须逐字保留
        "`\\vec{AB}`",
        "```\n\\vec{AB}\n```",
        "~~~~tex\n\\vec{AB}\n~~~~",
        # TikZ / verbatim 环境整块保留
        "\\begin{tikzpicture}\\vec{AB}\\end{tikzpicture}",
        "\\begin{verbatim}\\vec{AB}\\end{verbatim}",
        # 原样输出用的 HTML 与内容锁引用
        "<code>\\vec{AB}</code>",
        "<mathbank-math>\\vec{AB}</mathbank-math>",
        "[[MBM_1]]",
        "![插图](/static/uploads/\\vec.png)",
        # 纯文本宏参数不是向量
        r"\text{向量 AB}",
        r"\label{eq:vec}",
    ],
)
def test_protected_regions_are_never_rewritten(source):
    assert normalize_vector_notation(source) == source


def test_protected_regions_do_not_shield_neighbouring_math():
    source = "`\\vec{AB}` 但 $\\vec{CD}$ 要改"
    assert normalize_vector_notation(source) == (
        "`\\vec{AB}` 但 $\\overrightarrow{CD}$ 要改"
    )


def test_non_string_input_degrades_to_empty():
    assert normalize_vector_notation(None) == ""
    assert normalize_vector_notation("") == ""
