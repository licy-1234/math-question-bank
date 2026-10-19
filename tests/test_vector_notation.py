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
        # 单字母带上下标：一律改用拉伸箭头，下标写在内在外都归到同一形态
        (r"$\vec{v_{1}}$", r"$\overrightarrow{v_{1}}$"),
        (r"$\vec{v}_{1}$", r"$\overrightarrow{v_{1}}$"),
        (r"$\vec{v_{2}}=2\vec{CD}$", r"$\overrightarrow{v_{2}}=2\overrightarrow{CD}$"),
        (
            r"$\vec{v}_{1}\cdot \vec{v}_{2}=0$",
            r"$\overrightarrow{v_{1}}\cdot \overrightarrow{v_{2}}=0$",
        ),
        (r"$\vec{v_{12}}$", r"$\overrightarrow{v_{12}}$"),
        (r"$\vec{v}_{12}$", r"$\overrightarrow{v_{12}}$"),
        (r"$\vec{P_{1}}$", r"$\overrightarrow{P_{1}}$"),
        (r"$\vec{v_{1}^{(2)}}$", r"$\overrightarrow{v_{1}^{(2)}}$"),
        (r"$\vec{v_{1}}^{2}$", r"$\overrightarrow{v_{1}^{2}}$"),
        (r"$\vec{v_{i}^{2}}$", r"$\overrightarrow{v_{i}^{2}}$"),
        (r"$\vec{AB}_{1}$", r"$\overrightarrow{AB_{1}}$"),
        (r"$\vec{\alpha_{1}}$", r"$\overrightarrow{\alpha_{1}}$"),
        (r"$\vec{v_{\frac{1}{2}}}$", r"$\overrightarrow{v_{\frac{1}{2}}}$"),
        # 反向箭头
        (r"$\overset{\longleftarrow}{BA}$", r"$\overleftarrow{BA}$"),
        (r"$\overset{\leftarrow}{b}$", r"$\overleftarrow{b}$"),
        (r"$\overleftarrow{v}_{1}$", r"$\overleftarrow{v_{1}}$"),
        # 混合出现时逐处改写，运算与间距不动
        (
            r"$\vec{v}=\vec{AB}=2\vec{CD}$",
            r"$\vec{v}=\overrightarrow{AB}=2\overrightarrow{CD}$",
        ),
        # OCR 常见的 Unicode 组合箭头
        ("A\u20d7 与 B\u20d7", r"\vec{A} 与 \vec{B}"),
        # 归属不明的一律不动：基里还有运算符、裸下标超过一个原子、无花括号
        (r"$\vec{v_{1}+1}$", r"$\vec{v_{1}+1}$"),
        (r"$\vec v$", r"$\vec v$"),
        # 非向量重音不受影响
        (r"$\overline{AB}$ 与 $\hat{AB}$", r"$\overline{AB}$ 与 $\hat{AB}$"),
    ],
)
def test_vector_notation_is_standardized(source, expected):
    assert normalize_vector_notation(source) == expected


@pytest.mark.parametrize(
    "source",
    [
        # 基后面还有运算符：下标归属不明
        r"$\vec{v_{1}+1}$",
        r"$\vec{v_{\frac{1}{2}}+1}$",
        # 裸下标超过一个原子（\vec{v}_12），无法判断哪几个字符属于下标
        r"$\vec{v}_12$",
        # 完全没写花括号
        r"$\vec v$",
    ],
)
def test_ambiguous_spans_are_left_untouched(source):
    assert normalize_vector_notation(source) == source


def test_ambiguous_span_does_not_block_its_neighbours():
    source = r"$\vec{v_{1}+1}$ 与 $\vec{w_{2}}$"
    assert normalize_vector_notation(source) == (
        r"$\vec{v_{1}+1}$ 与 $\overrightarrow{w_{2}}$"
    )


@pytest.mark.parametrize(
    "source",
    [
        r"$\vec{PB}$ 与 $\vec {BP}$ 与 $\overset{\to}{AB}$",
        "A\u20d7 与 $\vec{a}$",
        r"$\vec{v_{1}}$ + $\overrightarrow {AB}$",
        r"$\vec{v}_{1}\cdot \vec{v}_{2}$",
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
