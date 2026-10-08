#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成留出集 holdout_cases.json（独立验证用，不参与任何调参）。

构造原则
--------
1. 知识点与 ``cases.json`` 尽量不重叠：优先覆盖 空间向量与立体几何、复数几何意义、
   计数原理/排列组合、条件概率与全概率、随机变量(超几何/正态)、成对数据统计分析
   (回归/列联表/独立性检验)、数学归纳法、直线与圆、导数与不等式综合 等。
2. 题干数字、表述与 cases.json 均不同。
3. 每类 ≥10 条，latex / plaintext 双形态覆盖。
4. 刻意保留真实试卷里会出现的「刁钻形态」，不做任何为了迎合规则层的美化。
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "holdout_cases.json"

CASES: list[dict] = []


def add(cid, form, difficulty, chapter_code, modality, source_hint, content, tricky=False, reason=""):
    CASES.append(
        {
            "id": cid,
            "form": form,
            "difficulty": difficulty,
            "chapter_code": chapter_code,
            "modality": modality,
            "source_hint": source_hint,
            "content": content,
            "tricky": bool(tricky),
            "tricky_reason": reason,
        }
    )


# ===========================================================================
# 一、单选题 12 条
# ===========================================================================

add(
    "SC-H01", "single_choice", "easy", "B2-C7-S1", "latex",
    "人教A必修二·复数的概念（几何意义）",
    "\n".join([
        r"设复数 $z$ 在复平面内对应的点为 $Z(x,y)$，且 $|z-2|=|z-2\mathrm{i}|$，则点 $Z$ 的轨迹方程为（　　）",
        r"① $x-y=0$",
        r"② $x+y=0$",
        r"③ $x+y-2=0$",
        r"④ $x-y-2=0$",
    ]),
    True, "选项用 ①②③④ 排版，无 A-D 字母、无 \\begin{choices}",
)

add(
    "SC-H02", "single_choice", "medium", "X3-C6-S2-P3", "plaintext",
    "人教A选择性必修三·组合",
    "\n".join([
        "甲、乙、丙、丁、戊五名同学合影，要求甲、乙两人必须站在一起，则不同的站法种数为（　　）",
        "甲. 24",
        "乙. 36",
        "丙. 48",
        "丁. 60",
    ]),
    True, "选项用 甲乙丙丁 排版；且题干里 甲乙丙丁 同时是人名，构成同形干扰",
)

add(
    "SC-H03", "single_choice", "medium", "X3-C7-S1-P1", "latex",
    "人教A选择性必修三·条件概率",
    "\n".join([
        r"某气象站记录显示，甲地当日降雨的概率为 $0.3$，乙地当日降雨的概率为 $0.4$，两地同时降雨的概率为 $0.12$。在甲地降雨的条件下，乙地降雨的概率为（　　）",
        r"\choice $0.30$",
        r"\choice $0.36$",
        r"\choice $0.40$",
        r"\choice $0.48$",
    ]),
    True, "选项用裸 \\choice 宏（无 \\begin{choices} 环境），最低出现次数阈值为 2",
)

add(
    "SC-H04", "single_choice", "easy", "X3-C7-S5", "plaintext",
    "人教A选择性必修三·正态分布",
    "\n".join([
        "某校高一数学统考成绩 X 近似服从正态分布 N(75,25)。若按A:B=2:3的比例将该年级学生分成两组，则 P(X≥75) 等于（　　）",
        "A. 0.2",
        "B. 0.3",
        "C. 0.5",
        "D. 0.8",
    ]),
    True, "题干含干扰串「按A:B=2:3」，A 后紧跟冒号",
)

add(
    "SC-H05", "single_choice", "medium", "X1-C1-S2", "latex",
    "人教A选择性必修一·空间向量基本定理",
    "\n".join([
        r"已知 $\vec{a},\vec{b},\vec{c}$ 是空间向量的一组基底，则下列向量中能与 $\vec{a},\vec{b}$ 一起构成空间另一组基底的是（　　）",
        r"\begin{tasks}(4)",
        r"\task $\vec{a}+\vec{b}$",
        r"\task $\vec{c}$",
        r"\task $2\vec{a}$",
        r"\task $\vec{a}-\vec{b}$",
        r"\end{tasks}",
    ]),
    True, "选项用 \\begin{tasks}\\task 宏排版，既无 A-D 字母也无 \\begin{choices}",
)

add(
    "SC-H06", "single_choice", "medium", "X1-C3-S1-P2", "latex",
    "人教A选择性必修一·椭圆的简单几何性质",
    "\n".join([
        r"已知椭圆C：$\dfrac{x^2}{4}+y^2=1$ 的左、右顶点分别为 $A_1,A_2$，点 $P$ 为椭圆上异于顶点的任一点，则直线 $PA_1$ 与 $PA_2$ 的斜率之积等于（　　）",
        r"A. $-\dfrac{1}{4}$",
        r"B. $\dfrac{1}{4}$",
        r"C. $-4$",
        r"D. $4$",
    ]),
    True, "题干含干扰串「椭圆C：」——C 紧跟全角冒号，且 C 前是汉字",
)

add(
    "SC-H07", "single_choice", "medium", "X3-C8-S3", "plaintext",
    "人教A选择性必修三·列联表与独立性检验",
    "\n".join([
        "为研究高中生每天使用手机时长与视力不良是否有关，随机抽取若干学生得到 2×2 列联表，计算得 χ²=6.635，则下列结论正确的是（　　）",
        "A. 有 99% 的把握认为两者有关",
        "B. 有 95% 的把握认为两者有关",
        "C. 有 90% 的把握认为两者有关",
        "D. 没有充分证据认为两者有关",
    ]),
)

add(
    "SC-H08", "single_choice", "easy", "X3-C8-S2", "latex",
    "人教A选择性必修三·一元线性回归模型",
    "\n".join([
        r"已知变量 $x$ 与 $y$ 具有线性相关关系，由样本数据求得回归直线方程为 $\hat{y}=1.5x+\hat{a}$，且样本点的中心为 $(4,9)$，则 $\hat{a}$ 等于（　　）",
        r"A．$1$",
        r"B．$2$",
        r"C．$3$",
        r"D．$4$",
    ]),
    True, "全角选项 A．B．C．D．，依赖全角句号命中",
)

add(
    "SC-H09", "single_choice", "easy", "X3-C6-S1", "plaintext",
    "人教A选择性必修三·分类加法与分步乘法计数原理",
    "\n".join([
        "已知集合A={1,2,3}，B={4,5,6,7}，从A、B中各取一个元素分别作为点的横坐标与纵坐标，则可确定不同点的个数为（　　）",
        "A. 7",
        "B. 12",
        "C. 16",
        "D. 24",
    ]),
    True, "题干含干扰串「集合A、B」与「从A、B中各取」，A/B 后紧跟顿号",
)

add(
    "SC-H10", "single_choice", "easy", "B1-C5-S1", "latex",
    "人教A必修一·任意角和弧度制",
    "\n".join([
        r"已知扇形 $OAB$ 的圆心角为 $2\ \mathrm{rad}$，半径为 $3$，则该扇形的面积为（　　）",
        r"A. $3$",
        r"B. $6$",
        r"C. $9$",
        r"D. $18$",
    ]),
)

add(
    "SC-H11", "single_choice", "easy", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"用数学归纳法证明 $1+2+2^2+\cdots+2^{n-1}=2^n-1$（$n\in\mathbb{N}^*$）时，第一步应验证（　　）",
        r"A. $n=0$ 时成立",
        r"B. $n=1$ 时成立",
        r"C. $n=2$ 时成立",
        r"D. $n=1$ 与 $n=2$ 时都成立",
    ]),
)

add(
    "SC-H12", "single_choice", "easy", "B1-C5-S5", "latex",
    "人教A必修一·三角恒等变换",
    "\n".join([
        r"设 $f(A)=\sin A+\cos A$，其中 $A$ 为 $\triangle ABC$ 的一个内角，则 $f(A)$ 的最大值为（　　）",
        r"A. $1$",
        r"B. $\sqrt{2}$",
        r"C. $\dfrac{\sqrt{3}}{2}$",
        r"D. $2$",
    ]),
    True, "题干含函数调用串 f(A)，A 紧跟右括号",
)


# ===========================================================================
# 二、多选题 12 条
# ===========================================================================

add(
    "MC-H01", "multi_choice", "medium", "X1-C1-S1-P2", "latex",
    "人教A选择性必修一·空间向量的数量积运算",
    "\n".join([
        r"在正方体 $ABCD-A_1B_1C_1D_1$ 中，下列结论正确的是（　　）",
        r"A. $\overrightarrow{AB}+\overrightarrow{BC}=\overrightarrow{AC}$",
        r"B. $\overrightarrow{AC_1}=\overrightarrow{AB}+\overrightarrow{AD}+\overrightarrow{AA_1}$",
        r"C. $\overrightarrow{AB}\cdot\overrightarrow{AD}=0$",
        r"D. $\overrightarrow{AC}\cdot\overrightarrow{BD}=0$",
    ]),
    True, "完全无「多选题」提示语，四个选项全真，只能靠语义判断为多选",
)

add(
    "MC-H02", "multi_choice", "medium", "B2-C7-S2", "latex",
    "人教A必修二·复数的四则运算",
    "\n".join([
        r"（多选题）已知复数 $z_1=1+\mathrm{i}$，$z_2=2-\mathrm{i}$，则下列结论正确的是（　　）",
        r"① $z_1+z_2=3$",
        r"② $z_1z_2=3+\mathrm{i}$",
        r"③ $\dfrac{z_1}{z_2}=\dfrac{1+3\mathrm{i}}{5}$",
        r"④ $|z_1z_2|=\sqrt{10}$",
    ]),
    True, "选项用 ①②③④ 排版 + 「（多选题）」提示语；圆圈数字会被小问计数器误计",
)

add(
    "MC-H03", "multi_choice", "medium", "X1-C2-S5-P1", "plaintext",
    "人教A选择性必修一·直线与圆的位置关系",
    "\n".join([
        "已知直线 l：y=kx+1 与圆 C：x²+y²-2x-3=0，全部选对得5分，部分选对得2分，则下列说法正确的是（　　）",
        "A. 圆 C 的圆心坐标为 (1,0)",
        "B. 圆 C 的半径为 2",
        "C. 直线 l 恒过定点 (0,1)",
        "D. 当 k=0 时，直线 l 被圆 C 截得的弦长为 2√3",
    ]),
    True, "多选题提示语是评分语「全部选对得5分，部分选对得2分」，题干无「多选题」字样",
)

add(
    "MC-H04", "multi_choice", "medium", "X3-C6-S2-P4", "latex",
    "人教A选择性必修三·组合数",
    "\n".join([
        r"在每小题给出的四个选项中，有多项符合题目要求。关于排列数 $A_n^m$ 与组合数 $C_n^m$（$n\geqslant m\geqslant1$），下列等式恒成立的有（　　）",
        r"甲. $C_n^m=C_n^{n-m}$",
        r"乙. $C_{n+1}^m=C_n^m+C_n^{m-1}$",
        r"丙. $A_n^m=nA_{n-1}^{m-1}$",
        r"丁. $A_n^m=C_n^m\cdot m!$",
    ]),
    True, "选项用 甲乙丙丁 排版 + 「在每小题给出的四个选项中，有多项符合题目要求」提示语",
)

add(
    "MC-H05", "multi_choice", "easy", "X3-C7-S5", "plaintext",
    "人教A选择性必修三·正态分布",
    "\n".join([
        "已知随机变量 X 服从正态分布 N(μ,σ²)，则下列结论正确的是（　　）",
        "A. 正态密度曲线关于直线 x=μ 对称",
        "B. P(X≥μ)=0.5",
        "C. σ 越小，正态密度曲线越矮胖",
        "D. P(μ-σ≤X≤μ+σ)≈0.6827",
    ]),
    True, "完全无提示语；A、B、D 为真，C 为假，只能靠语义判断为多选",
)

add(
    "MC-H06", "multi_choice", "medium", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"选出所有满足条件的选项：下列命题中，适宜用数学归纳法证明的有（　　）",
        r"A. $1+3+5+\cdots+(2n-1)=n^2$（$n\in\mathbb{N}^*$）",
        r"B. $1+\dfrac{1}{2}+\dfrac{1}{3}+\cdots+\dfrac{1}{n}<\ln n+1$（$n\geqslant2$）",
        r"C. $n^2+n+41$ 对任意 $n\in\mathbb{N}^*$ 都是质数",
        r"D. $2^n>n^2$（$n\geqslant5$）",
    ]),
    True, "提示语是「选出所有满足条件的选项」而非「多选题」",
)

add(
    "MC-H07", "multi_choice", "hard", "X1-C1-S4-P2", "latex",
    "人教A选择性必修一·距离、夹角问题",
    "\n".join([
        r"（多选题）在棱长为 $2$ 的正方体 $ABCD-A_1B_1C_1D_1$ 中，下列结论正确的是（　　）",
        r"\begin{tasks}(4)",
        r"\task 直线 $AC_1$ 与平面 $ABCD$ 所成角的正弦值为 $\dfrac{\sqrt{3}}{3}$",
        r"\task 点 $A$ 到平面 $BC_1D$ 的距离为 $\dfrac{2\sqrt{3}}{3}$",
        r"\task 异面直线 $AC$ 与 $BD_1$ 所成的角为 $90^\circ$",
        r"\task 三棱锥 $A-BC_1D$ 的体积为 $\dfrac{4}{3}$",
        r"\end{tasks}",
    ]),
    True, "选项用 \\begin{tasks}\\task 宏排版 + 「（多选题）」提示语",
)

add(
    "MC-H08", "multi_choice", "easy", "X3-C8-S1", "plaintext",
    "人教A选择性必修三·成对数据的统计相关性",
    "\n".join([
        "关于两个变量 x 与 y 的样本相关系数 r，下列说法正确的是（　　）选对但不全得2分，有选错的得0分。",
        "A. |r|≤1",
        "B. r 越接近 1，线性相关程度越强",
        "C. r>0 时称 x 与 y 正相关",
        "D. r=0 时 x 与 y 一定不存在任何关系",
    ]),
    True, "提示语是变体评分语「选对但不全得2分」，非「部分选对得2分」",
)

add(
    "MC-H09", "multi_choice", "medium", "X1-C2-S5-P2", "latex",
    "人教A选择性必修一·圆与圆的位置关系",
    "\n".join([
        r"已知圆 $C_1$：$x^2+y^2=4$ 与圆 $C$：$x^2+y^2-4x-2y+1=0$，则下列说法正确的是（　　）",
        r"A. 圆 $C$ 的半径为 $2$",
        r"B. 两圆公共弦所在直线的方程为 $4x+2y-5=0$",
        r"C. 两圆外切",
        r"D. 两圆的圆心距为 $\sqrt{5}$",
    ]),
    True, "完全无提示语；A、B、D 为真，C 为假；题干另有「圆C：」干扰串",
)

add(
    "MC-H10", "multi_choice", "hard", "B1-C1-S3", "latex",
    "人教A必修一·集合的基本运算（含参）",
    "\n".join([
        r"（多选题）已知全集 $U=\mathbb{R}$，集合 $A=\{x\mid x<1\}$，$B=\{x\mid x>a\}$，则关于 $\complement_U(A\cup B)$ 的说法正确的是（　　）",
        r"A. 当 $a=2$ 时，$\complement_U(A\cup B)=\varnothing$",
        r"B. 当 $a<1$ 时，$\complement_U(A\cup B)=\varnothing$",
        r"C. 当 $a=1$ 时，$\complement_U(A\cup B)=\{1\}$",
        r"D. 对任意实数 $a$，$\complement_U(A\cup B)\neq\mathbb{R}$",
    ]),
    True, "题干含干扰串 ∁_U(A∪B)=，其中 A、B 紧跟右括号/运算符",
)

add(
    "MC-H11", "multi_choice", "medium", "X2-C4-S2-P2", "plaintext",
    "人教A选择性必修二·等差数列的前n项和公式",
    "\n".join([
        "本题有多个正确选项，请全部选出。记 S_n 为等差数列 {a_n} 的前 n 项和，已知 S_5=S_9，则（　　）",
        "A. a_7=0",
        "B. S_7 是 S_n 的最大值或最小值",
        "C. 首项 a_1 与公差 d 符号相反",
        "D. S_14=0",
    ]),
    True, "提示语是「本题有多个正确选项，请全部选出」，非标准「多选题」字样",
)

add(
    "MC-H12", "multi_choice", "medium", "X2-C5-S3-P1", "latex",
    "人教A选择性必修二·导数在研究函数中的应用",
    "\n".join([
        r"已知函数 $f(x)=x\mathrm{e}^{x}$，则下列结论正确的是（　　）",
        r"A. $f(x)$ 在 $(-1,+\infty)$ 上单调递增",
        r"B. $f(x)$ 的极小值为 $-\dfrac{1}{\mathrm{e}}$",
        r"C. 曲线 $y=f(x)$ 在点 $(0,0)$ 处的切线方程为 $y=x$",
        r"D. 方程 $f(x)=a$ 有两个不同实根时，$a\in\left(-\dfrac{1}{\mathrm{e}},0\right)$",
    ]),
    True, "完全无提示语；四个选项全真，只能靠语义判断为多选",
)


# ===========================================================================
# 三、填空题 12 条
# ===========================================================================

add(
    "FB-H01", "fill_in_blank", "easy", "X1-C1-S3-P2", "latex",
    "人教A选择性必修一·空间向量运算的坐标表示",
    "\n".join([
        r"已知空间三点 $A(1,0,2)$，$B(2,1,3)$，$C(0,3,1)$，则 $\overrightarrow{AB}\cdot\overrightarrow{AC}=$\fillin",
    ]),
    True, "空位用 \\fillin 宏；题干三个点名 A、B、C 紧跟左括号",
)

add(
    "FB-H02", "fill_in_blank", "medium", "X3-C6-S2-P1", "plaintext",
    "人教A选择性必修三·排列",
    "\n".join([
        "由数字 1、2、3、4、5 组成没有重复数字的五位数，其中大于 30000 的共有［　　］个。",
    ]),
    True, "空位用全角方括号 ［　　］（内含两个全角空格）",
)

add(
    "FB-H03", "fill_in_blank", "easy", "B2-C7-S2", "latex",
    "人教A必修二·复数的四则运算",
    "\n".join([
        r"设复数 $z$ 满足 $z(2-\mathrm{i})=3+4\mathrm{i}$，则 $z$ 的实部为$\underline{\quad}$，虚部为$\underline{\quad}$。",
    ]),
    True, "两空填空题，空位用 \\underline{\\quad}",
)

add(
    "FB-H04", "fill_in_blank", "easy", "B2-C6-S4", "plaintext",
    "人教A必修二·正弦定理余弦定理（解三角形）",
    "\n".join([
        "在△ABC中，角A、B、C的对边分别为a、b、c，已知 a cos B + b cos A = 2c cos C，且 c=2，则△ABC面积的最大值为__________。",
    ]),
    True, "题干含干扰串「角A、B、C的对边」，A、B 紧跟顿号；且含 a cos B 这类字母紧跟运算符的文本",
)

add(
    "FB-H05", "fill_in_blank", "medium", "X3-C7-S5", "plaintext",
    "人教A选择性必修三·正态分布",
    "\n".join([
        "已知随机变量 X 服从正态分布 N(3,4)，则 P(1<X<5)=（    ）。（参考数据：P(μ-σ<X<μ+σ)≈0.6827）",
    ]),
    True, "空位用半角括号内含四个空格 （    ）；阈值要求 ≥4 个空格",
)

add(
    "FB-H06", "fill_in_blank", "medium", "X3-C8-S3", "latex",
    "人教A选择性必修三·列联表与独立性检验",
    "\n".join([
        r"在一项独立性检验中，计算得 $\chi^2\approx3.841$，则在犯错误的概率不超过 ______ 的前提下可以认为两个分类变量有关。",
    ]),
    True, "空位用六个半角下划线 ______",
)

add(
    "FB-H07", "fill_in_blank", "medium", "X3-C6-S1", "plaintext",
    "人教A选择性必修三·分步乘法计数原理",
    "\n".join([
        "把答案填在横线上：已知集合A={x∈N|1≤x≤5}，B={-1,0,1}，从A、B中各取一个元素分别作为点 P 的横坐标与纵坐标，则这样的点共有____个。",
    ]),
    True, "「把答案填在横线上」提示语 + 干扰串「集合A、B」「从A、B中各取」",
)

add(
    "FB-H08", "fill_in_blank", "medium", "X2-C5-S2-P3", "latex",
    "人教A选择性必修二·简单复合函数的导数",
    "\n".join([
        r"设函数 $f(x)=\ln(2x+1)$，则 $f'(x)=$\underline{\qquad}，曲线 $y=f(x)$ 在 $x=0$ 处的切线方程为\underline{\qquad}。",
    ]),
    True, "两空填空题，空位用 \\underline{\\qquad}",
)

add(
    "FB-H09", "fill_in_blank", "medium", "X1-C3-S1-P2", "plaintext",
    "人教A选择性必修一·椭圆的简单几何性质",
    "\n".join([
        "已知椭圆C：x²/4+y²=1 的左焦点为 F，过 F 且斜率为 1 的直线与椭圆交于 M、N 两点，则线段 MN 的长为__________。",
    ]),
    True, "题干含干扰串「椭圆C：」；空位用十个半角下划线",
)

add(
    "FB-H10", "fill_in_blank", "easy", "B2-C9-S1", "plaintext",
    "人教A必修二·随机抽样",
    "\n".join([
        "某校有高中生 900 人、初中生 600 人，现按A:B=2:3的比例从高中生与初中生中抽取一个容量为 50 的样本，则应抽取高中生__________人。",
    ]),
    True, "题干含干扰串「按A:B=2:3」。",
)

add(
    "FB-H11", "fill_in_blank", "hard", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"用数学归纳法证明 $1+\dfrac{1}{2}+\dfrac{1}{3}+\cdots+\dfrac{1}{2^n-1}<n$（$n\geqslant2$，$n\in\mathbb{N}^*$）时，从 $n=k$ 递推到 $n=k+1$，不等式左边需要增添的项数为\fillin",
    ]),
    True, "空位用 \\fillin 宏；题面含多个分式宏",
)

add(
    "FB-H12", "fill_in_blank", "medium", "B2-C8-S3", "plaintext",
    "人教A必修二·简单几何体的表面积与体积",
    "\n".join([
        "在棱长为 4 的正方体 ABCD-A₁B₁C₁D₁ 中，D、E分别为棱 AA₁、CC₁ 的中点，则三棱锥 D-EB₁C₁ 的体积为__________。",
    ]),
    True, "题干含干扰串「D、E分别为…中点」，D、E 紧跟顿号",
)


# ===========================================================================
# 四、解答题 12 条
# ===========================================================================

add(
    "DA-H01", "detailed_answer", "hard", "X2-C5-S3-P1", "latex",
    "人教A选择性必修二·导数在研究函数中的应用（含参讨论）",
    "\n".join([
        r"设函数 $h(x)=\mathrm{e}^{x}-ax-1$（$a$ 为实常数）。",
        r"（1）当 $a=2$ 时，求曲线 $y=h(x)$ 在点 $(0,h(0))$ 处的切线方程；",
        r"（2）若对任意实数 $x$ 都有 $h(x)\geqslant0$ 成立，求 $a$ 的取值集合。",
    ]),
)

add(
    "DA-H02", "detailed_answer", "hard", "X1-C1-S4-P2", "plaintext",
    "人教A选择性必修一·空间向量的应用（三小问）",
    "\n".join([
        "如图，在四棱锥 P-ABCD 中，底面 ABCD 为菱形，∠BAD=60°，PA⊥底面 ABCD，PA=AB=2，D、E分别为棱 PB、PC 的中点。",
        "（1）证明：DE∥平面 ABCD；",
        "（2）求直线 PD 与底面 ABCD 所成角的大小；",
        "（3）求点 A 到平面 PBC 的距离。",
    ]),
    True, "题干含干扰串「D、E分别为棱 PB、PC 的中点」",
)

add(
    "DA-H03", "detailed_answer", "medium", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"用数学归纳法证明：对任意 $n\in\mathbb{N}^*$，都有",
        r"\[1\cdot2+2\cdot3+\cdots+n(n+1)=\dfrac{n(n+1)(n+2)}{3}。\]",
    ]),
)

add(
    "DA-H04", "detailed_answer", "medium", "X3-C8-S2", "plaintext",
    "人教A选择性必修三·一元线性回归模型及其应用",
    "\n".join([
        "为研究某农作物施肥量 x（单位：kg）与产量 y（单位：kg）的关系，测得 5 组数据：(1,30)，(2,45)，(3,55)，(4,70)，(5,80)。",
        "（1）求 y 关于 x 的回归直线方程（系数保留两位小数）；",
        "（2）据此估计施肥量为 6 kg 时的产量。",
    ]),
    True, "题干含 5 组形如 (1,30) 的坐标串，半角括号紧跟数字",
)

add(
    "DA-H05", "detailed_answer", "medium", "B1-C1-S4-P2", "latex",
    "人教A必修一·充分条件与必要条件（集合载体）",
    "\n".join([
        r"已知全集 $U=\mathbb{R}$，集合 $P=\{x\mid x^2-5x+6\leqslant0\}$，$Q=\{x\mid m-1\leqslant x\leqslant2m+1\}$。",
        r"（1）求 $\complement_U P$；",
        r"（2）若“$x\in Q$”是“$x\in P$”的必要条件，求实数 $m$ 的取值范围。",
    ]),
    True, "题干含干扰串 ∁_U P（补集符号紧跟集合名）",
)

add(
    "DA-H06", "detailed_answer", "medium", "X1-C2-S5-P1", "plaintext",
    "人教A选择性必修一·直线与圆的位置关系（含参）",
    "\n".join([
        "已知圆 C 经过点 M(2,-1)、N(4,3)，且圆心在直线 y=2x-4 上。",
        "（1）求圆 C 的方程；",
        "（2）若直线 l：x+y+t=0 与圆 C 交于 P、Q 两点，且 |PQ|=2√3，求实数 t 的值。",
    ]),
)

add(
    "DA-H07", "detailed_answer", "medium", "X3-C7-S4-P2", "latex",
    "人教A选择性必修三·超几何分布",
    "\n".join([
        r"一批零件共 $20$ 件，其中不合格品 $5$ 件，从中随机抽取 $3$ 件。",
        r"（1）求抽取的 $3$ 件中恰有 $1$ 件不合格品的概率；",
        r"（2）记抽取的 $3$ 件中不合格品的件数为 $X$，求 $X$ 的分布列与数学期望。",
    ]),
)

add(
    "DA-H08", "detailed_answer", "easy", "B2-C10-S1", "plaintext",
    "人教A必修二·随机事件与概率",
    "\n".join([
        "甲、乙两人下棋，和棋的概率为 1/2，乙获胜的概率为 1/3。",
        "① 求甲获胜的概率；",
        "② 求甲不输的概率；",
        "③ 判断事件“甲获胜”与事件“乙获胜”是否为互斥事件，并说明理由。",
    ]),
    True, "解答题的小问用 ①②③ 编号（而非（1）（2）（3）），圆圈数字会被选项扫描器当作选项标记",
)

add(
    "DA-H09", "detailed_answer", "medium", "X2-C4-S1", "latex",
    "人教A选择性必修二·数列的概念",
    "\n".join([
        r"已知数列 $\{a_n\}$ 的前 $n$ 项和为 $S_n=2n^2-3n$。",
        r"\begin{enumerate}",
        r"\item 求 $\{a_n\}$ 的通项公式；",
        r"\item 求数列 $\{|a_n|\}$ 的前 $6$ 项和；",
        r"\item 判断 $\{a_n\}$ 是否为等差数列，并说明理由。",
        r"\end{enumerate}",
    ]),
    True, "解答题的小问用 \\begin{enumerate}+\\item 排版（≥3 个 item）",
)

add(
    "DA-H10", "detailed_answer", "hard", "X3-C6-S2-P4", "plaintext",
    "人教A选择性必修三·组合数（分组分配）",
    "\n".join([
        "甲、乙、丙、丁、戊 5 名同学报名参加学校的三个不同社团，每人限报一个社团，且每个社团至少有一人报名。",
        "（1）求不同的报名方法种数；",
        "（2）若甲、乙两人不能报同一个社团，求不同的报名方法种数。",
    ]),
    True, "题干里 甲乙丙丁 是人名且紧跟顿号，与选项标记同形",
)

add(
    "DA-H11", "detailed_answer", "hard", "B1-C2-S2", "latex",
    "人教A必修一·基本不等式",
    "\n".join([
        r"已知 $a>0$，$b>0$，且 $a+b=1$。",
        r"（1）证明：$\dfrac{1}{a}+\dfrac{4}{b}\geqslant9$；",
        r"（2）求 $\dfrac{1}{a^2}+\dfrac{1}{b^2}$ 的最小值。",
    ]),
)

add(
    "DA-H12", "detailed_answer", "medium", "B2-C6-S4", "plaintext",
    "人教A必修二·正弦定理余弦定理（解三角形）",
    "\n".join([
        "在△ABC中，角A、B、C的对边分别为a、b、c，记 f(A)=2sin A cos A。",
        "（1）若 f(A)=1，求角 A 的大小；",
        "（2）在（1）的条件下，若 a=3，b+c=5，求△ABC的面积。",
    ]),
    True, "同时含「角A、B、C的对边」与函数调用串 f(A) 两类干扰",
)


def main() -> None:
    payload = {
        "_meta": {
            "name": "留出集（独立验证用，未参与任何阈值/术语表调参）",
            "created_by": "verifier",
            "note": (
                "与 tools/classify_eval/cases.json 无重叠：知识点优先取 "
                "空间向量与立体几何、复数几何意义、计数原理/排列组合、条件概率、"
                "随机变量(超几何/正态)、成对数据统计分析、数学归纳法、直线与圆、"
                "导数与不等式综合；题干数字与表述均重新编写。"
            ),
        },
        "cases": CASES,
    }
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    print(f"written {OUT}  cases={len(CASES)}")
    from collections import Counter
    print("by form   :", dict(Counter(c["form"] for c in CASES)))
    print("by modality:", dict(Counter(c["modality"] for c in CASES)))
    print("tricky    :", sum(1 for c in CASES if c["tricky"]))


if __name__ == "__main__":
    main()
