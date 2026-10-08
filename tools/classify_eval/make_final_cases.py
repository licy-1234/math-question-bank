#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成第三份验收集 final_cases.json（终验用，未参与任何调参）。

加码方向（针对前两轮暴露的薄弱形态）
--------------------------------
* 多选题：6 条**题干完全无多选提示语**，只能靠语义判断；
* 解答题：小问分别用 ①②③ / ``\\begin{enumerate}+\\item`` / ``(1)(2)(3)`` 编号；
* 天干「甲、乙、丙、丁、戊、己」作人名/箱名而非选项的干扰；
* 「角A、B、C的对边」「集合A、B」「椭圆C：」「∁_U(A∪B)」「按A:B=2:3」
  「交于A、B两点」「D、E分别为…中点」等同形干扰；
* 填空题覆盖 \\fillin / \\underline{\\quad} / __________ / （    ） / ［　　］ / 多空 / 提示语。

知识点继续避开前两集，往 条件概率与全概率、正态分布、成对数据统计、
数学归纳法、计数原理、圆锥曲线综合、导数与函数零点、解三角形、空间向量 出题。
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "final_cases.json"

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
    "SC-F01", "single_choice", "medium", "X3-C7-S1-P2", "latex",
    "人教A选择性必修三·全概率公式",
    "\n".join([
        r"某工厂有甲、乙两条生产线，甲线产量占总产量的 $60\%$，乙线占 $40\%$；"
        r"甲线与乙线的次品率分别为 $1\%$ 与 $2\%$。现从当日产品中随机抽取一件，则该件为次品的概率为（　　）",
        r"A. $0.012$",
        r"B. $0.014$",
        r"C. $0.016$",
        r"D. $0.020$",
    ]),
    True, "题干含天干「甲、乙」作生产线名且紧跟顿号",
)

add(
    "SC-F02", "single_choice", "medium", "X3-C7-S5", "plaintext",
    "人教A选择性必修三·正态分布",
    "\n".join([
        "某地区高三男生身高 X 近似服从正态分布 N(170,36)。已知 P(X>176)=0.1587，则 P(164<X<170) 的值为（　　）",
        "A. 0.1587",
        "B. 0.3413",
        "C. 0.6826",
        "D. 0.8413",
    ]),
)

add(
    "SC-F03", "single_choice", "easy", "X1-C1-S3-P1", "latex",
    "人教A选择性必修一·空间直角坐标系",
    "\n".join([
        r"在空间直角坐标系 $Oxyz$ 中，点 $M(2,-1,3)$ 关于坐标平面 $xOy$ 对称的点的坐标是（　　）",
        r"A. $(2,-1,-3)$",
        r"B. $(-2,-1,3)$",
        r"C. $(2,1,3)$",
        r"D. $(-2,1,-3)$",
    ]),
)

add(
    "SC-F04", "single_choice", "medium", "X3-C6-S2-P3", "plaintext",
    "人教A选择性必修三·组合",
    "\n".join([
        "某班有 6 名男生与 4 名女生，要从中选出 3 人参加座谈会，且要求男、女生都要有人参加，则不同的选法种数为（　　）",
        "A. 96",
        "B. 100",
        "C. 116",
        "D. 120",
    ]),
)

add(
    "SC-F05", "single_choice", "medium", "X1-C3-S1-P1", "latex",
    "人教A选择性必修一·椭圆及其标准方程",
    "\n".join([
        r"已知椭圆C：$\dfrac{x^2}{9}+\dfrac{y^2}{5}=1$ 的两个焦点为 $F_1$、$F_2$，"
        r"过 $F_1$ 的直线与椭圆交于 $P$、$Q$ 两点，则 $\triangle PQF_2$ 的周长为（　　）",
        r"A. $6$",
        r"B. $8$",
        r"C. $12$",
        r"D. $16$",
    ]),
    True, "题干含干扰串「椭圆C：」（C 紧跟全角冒号且前接汉字）",
)

add(
    "SC-F06", "single_choice", "medium", "X3-C8-S2", "latex",
    "人教A选择性必修三·一元线性回归模型",
    "\n".join([
        r"在一次线性回归分析中，求得决定系数 $R^2=0.86$，残差平方和为 $140$，则总偏差平方和为（　　）",
        r"A. $120.4$",
        r"B. $1000$",
        r"C. $140.86$",
        r"D. $164$",
    ]),
)

add(
    "SC-F07", "single_choice", "medium", "X2-C5-S3-P2", "latex",
    "人教A选择性必修二·函数的极值与最大(小)值",
    "\n".join([
        r"已知函数 $f(x)=x^3-6x^2+9x+1$，则 $f(x)$ 的极大值与极小值之差等于（　　）",
        r"A. $2$",
        r"B. $4$",
        r"C. $6$",
        r"D. $8$",
    ]),
)

add(
    "SC-F08", "single_choice", "medium", "B2-C6-S4", "plaintext",
    "人教A必修二·正弦定理余弦定理（解三角形）",
    "\n".join([
        "在△ABC中，角A、B、C的对边分别为a、b、c，若 sin A : sin B : sin C = 3 : 5 : 7，则这个三角形最大内角的余弦值为（　　）",
        "A. -1/2",
        "B. -1/5",
        "C. 1/2",
        "D. 11/14",
    ]),
    True, "题干含干扰串「角A、B、C的对边」，A、B 紧跟顿号",
)

add(
    "SC-F09", "single_choice", "easy", "X1-C1-S1-P2", "latex",
    "人教A选择性必修一·空间向量的数量积运算",
    "\n".join([
        r"已知空间向量 $\vec{m}=(1,1,0)$，$\vec{n}=(1,0,1)$，则 $\vec{m}$ 与 $\vec{n}$ 的夹角大小为（　　）",
        r"A. $30^\circ$",
        r"B. $45^\circ$",
        r"C. $60^\circ$",
        r"D. $90^\circ$",
    ]),
)

add(
    "SC-F10", "single_choice", "easy", "X3-C6-S1", "plaintext",
    "人教A选择性必修三·分步乘法计数原理",
    "\n".join([
        "已知集合A={2,4,6,8}，B={1,3,5}，从A、B中各取一个数字，用 A 中的数字作十位、B 中的数字作个位，则可组成不同两位数的个数为（　　）",
        "A. 7",
        "B. 12",
        "C. 15",
        "D. 24",
    ]),
    True, "题干含干扰串「集合A、B」与「从A、B中各取」",
)

add(
    "SC-F11", "single_choice", "hard", "X1-C3-S3-P2", "plaintext",
    "人教A选择性必修一·抛物线的简单几何性质",
    "\n".join([
        "已知抛物线 y²=8x 的焦点为 F，过 F 的直线与抛物线交于A、B两点，且 |AB|=10，则线段 AB 的中点到 y 轴的距离为（　　）",
        "A. 2",
        "B. 3",
        "C. 4",
        "D. 5",
    ]),
    True, "题干含干扰串「交于A、B两点」",
)

add(
    "SC-F12", "single_choice", "easy", "B2-C8-S3", "plaintext",
    "人教A必修二·简单几何体的表面积与体积",
    "\n".join([
        "已知一个圆锥的底面半径为 3，母线长为 5，则该圆锥的侧面积等于（　　）",
        "A. 12π",
        "B. 15π",
        "C. 20π",
        "D. 24π",
    ]),
)


# ===========================================================================
# 二、多选题 12 条（前 6 条刻意不带任何多选提示语）
# ===========================================================================

add(
    "MC-F01", "multi_choice", "medium", "B1-C3-S3", "latex",
    "人教A必修一·幂函数",
    "\n".join([
        r"关于幂函数 $y=x^{\alpha}$，下列结论正确的是（　　）",
        r"A. 当 $\alpha=-1$ 时，函数图象关于坐标原点对称",
        r"B. 当 $\alpha=\dfrac12$ 时，函数的定义域为 $[0,+\infty)$",
        r"C. 当 $\alpha=2$ 时，函数在 $(-\infty,0)$ 上单调递减",
        r"D. 任意幂函数的图象都经过点 $(1,1)$",
    ]),
    True, "完全无多选提示语，四项全真，只能靠语义判断；知识点「幂函数」前三集均未出现",
)

add(
    "MC-F02", "multi_choice", "medium", "X3-C7-S5", "latex",
    "人教A选择性必修三·正态分布",
    "\n".join([
        r"已知随机变量 $X\sim N(2,\sigma^2)$（$\sigma>0$），且 $P(X<4)=0.8$，则下列等式成立的是（　　）",
        r"A. $P(X>0)=0.8$",
        r"B. $P(0<X<2)=0.3$",
        r"C. $P(X>2)=0.5$",
        r"D. $P(2<X<4)=0.4$",
    ]),
    True, "完全无多选提示语，正确项为 A、B、C，只能靠语义判断",
)

add(
    "MC-F03", "multi_choice", "medium", "X1-C2-S5-P2", "plaintext",
    "人教A选择性必修一·圆与圆的位置关系",
    "\n".join([
        "已知圆 C₁：x²+y²=9 与圆 C₂：(x-4)²+y²=1，则下列结论正确的是（　　）",
        "A. 两圆的圆心距为 4",
        "B. 两圆外切",
        "C. 两圆共有三条公切线",
        "D. 两圆的半径之差为 1",
    ]),
    True, "完全无多选提示语，正确项为 A、B、C，只能靠语义判断",
)

add(
    "MC-F04", "multi_choice", "hard", "X2-C5-S3-P1", "latex",
    "人教A选择性必修二·导数在研究函数中的应用",
    "\n".join([
        r"设 $g(t)=\dfrac{\ln t}{t}$（$t>0$）。下列判断中能成立的有（　　）",
        r"A. $g(t)$ 在 $(0,\mathrm{e})$ 上递增",
        r"B. $g(t)$ 的最大值是 $\dfrac{1}{\mathrm{e}}$",
        r"C. 方程 $g(t)=\dfrac13$ 恰有两个实根",
        r"D. 不等式 $g(t)\leqslant\dfrac13$ 对一切 $t>0$ 恒成立",
    ]),
    True, "完全无多选提示语，正确项为 A、B、C，只能靠语义判断",
)

add(
    "MC-F05", "multi_choice", "medium", "X2-C4-S2", "latex",
    "人教A选择性必修二·等差数列",
    "\n".join([
        r"记 $S_n$ 为等差数列 $\{a_n\}$ 的前 $n$ 项和。已知 $a_2=2$，$a_3+a_7=10$，则下列结论正确的是（　　）",
        r"A. 公差 $d=1$",
        r"B. $a_5=5$",
        r"C. $S_5=25$",
        r"D. $S_9=45$",
    ]),
    True, "完全无多选提示语，正确项为 A、B、D，只能靠语义判断",
)

add(
    "MC-F06", "multi_choice", "hard", "X1-C3-S2-P2", "latex",
    "人教A选择性必修一·双曲线的简单几何性质",
    "\n".join([
        r"已知双曲线 $\dfrac{x^2}{4}-\dfrac{y^2}{12}=1$ 的左、右焦点分别是 $F_1$、$F_2$，点 $P$ 在该双曲线的右支上，则（　　）",
        r"A. 该双曲线的离心率为 $2$",
        r"B. 该双曲线的渐近线方程为 $y=\pm\sqrt{3}\,x$",
        r"C. 若 $PF_1\perp PF_2$，则 $\triangle PF_1F_2$ 的面积为 $12$",
        r"D. $|PF_1|-|PF_2|=2$",
    ]),
    True, "完全无多选提示语，正确项为 A、B、C，只能靠语义判断",
)

add(
    "MC-F07", "multi_choice", "medium", "X3-C7-S1-P1", "latex",
    "人教A选择性必修三·条件概率",
    "\n".join([
        r"（多选题）设 $A$、$B$ 为两个随机事件，且 $P(A)=0.6$，$P(B)=0.5$，$P(A\cup B)=0.8$，则下列说法正确的是（　　）",
        r"A. $P(A\cap B)=0.3$",
        r"B. 事件 $A$ 与事件 $B$ 相互独立",
        r"C. $P(A\mid B)=0.6$",
        r"D. $P(\bar{A}\cap\bar{B})=0.2$",
    ]),
)

add(
    "MC-F08", "multi_choice", "medium", "X3-C8-S1", "latex",
    "人教A选择性必修三·成对数据的统计相关性",
    "\n".join([
        r"在每小题给出的四个选项中，有多项符合题目要求。关于两个变量的样本相关系数 $r$ 与回归直线，下列说法正确的是（　　）",
        r"A. $|r|$ 越接近 $1$，两个变量的线性相关程度越强",
        r"B. 回归直线一定经过样本点的中心 $(\bar{x},\bar{y})$",
        r"C. 相关系数 $r$ 与回归直线斜率的符号一致",
        r"D. 去掉一个离群点后，回归直线一定保持不变",
    ]),
    True, "提示语为「在每小题给出的四个选项中，有多项符合题目要求」",
)

add(
    "MC-F09", "multi_choice", "medium", "B2-C8-S6", "latex",
    "人教A必修二·空间直线、平面的垂直",
    "\n".join([
        r"已知直线 $m$、$n$ 与平面 $\alpha$、$\beta$，全部选对得5分，部分选对得2分，则下列命题中成立的是（　　）",
        r"A. 若 $m\perp\alpha$，$n\perp\alpha$，则 $m\parallel n$",
        r"B. 若 $m\parallel\alpha$，$m\subset\beta$，$\alpha\cap\beta=n$，则 $m\parallel n$",
        r"C. 若 $\alpha\perp\beta$，$m\subset\alpha$，则 $m\perp\beta$",
        r"D. 若 $m\parallel\alpha$，$n\parallel\alpha$，则 $m\parallel n$",
    ]),
    True, "提示语为评分语「全部选对得5分，部分选对得2分」，题干无「多选题」字样",
)

add(
    "MC-F10", "multi_choice", "medium", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"本题有多个正确选项，请全部选出。关于数学归纳法，下列说法妥当的是（　　）",
        r"A. 第一步必须验证 $n=1$ 时命题成立",
        r"B. 归纳假设是“假设 $n=k$（$k\geqslant n_0$）时命题成立”",
        r"C. 由 $n=k$ 推 $n=k+1$ 时必须用到归纳假设",
        r"D. 只要由 $n=k$ 成立能推出 $n=k+1$ 成立，命题就对一切正整数成立",
    ]),
    True, "提示语为「本题有多个正确选项，请全部选出」",
)

add(
    "MC-F11", "multi_choice", "easy", "X3-C6-S2-P2", "plaintext",
    "人教A选择性必修三·排列数",
    "\n".join([
        "选出所有满足条件的选项：从 5 本互不相同的书中选出 3 本分给 3 名同学，每人 1 本，则下列表述正确的是（　　）",
        "A. 不同的分法共有 60 种",
        "B. 不同的分法共有 A_5^3 种",
        "C. 若只选不分，则有 C_5^3 种选法",
        "D. 不同的分法也可以表示为 C_5^3·A_3^3 种",
    ]),
    True, "提示语为「选出所有满足条件的选项」",
)

add(
    "MC-F12", "multi_choice", "hard", "X2-C5-S3-P2", "latex",
    "人教A选择性必修二·函数的极值与最大(小)值",
    "\n".join([
        r"（多选题）已知函数 $f(x)=x^3-3x^2+2$，则下列说法正确的是（　　）",
        r"A. $f(x)$ 恰有两个极值点",
        r"B. $f(x)$ 的极大值为 $2$",
        r"C. 方程 $f(x)=0$ 有三个互不相同的实根",
        r"D. $f(x)$ 的图象关于点 $(1,0)$ 中心对称",
    ]),
)


# ===========================================================================
# 三、填空题 12 条
# ===========================================================================

add(
    "FB-F01", "fill_in_blank", "easy", "X1-C1-S3-P2", "latex",
    "人教A选择性必修一·空间向量运算的坐标表示",
    "\n".join([
        r"已知空间向量 $\vec{p}=(3,-2,1)$，$\vec{q}=(-1,4,2)$，则 $2\vec{p}-\vec{q}=$\fillin",
    ]),
    True, "空位用 \\fillin 宏",
)

add(
    "FB-F02", "fill_in_blank", "medium", "X3-C6-S2-P3", "plaintext",
    "人教A选择性必修三·组合",
    "\n".join([
        "从 5 名男生与 3 名女生中选出 4 人参加辩论赛，要求至少有 1 名女生入选，则不同的选法共有［　　］种。",
    ]),
    True, "空位用全角方括号 ［　　］",
)

add(
    "FB-F03", "fill_in_blank", "medium", "X3-C7-S1-P1", "latex",
    "人教A选择性必修三·条件概率",
    "\n".join([
        r"袋中装有 4 个红球与 6 个白球，不放回地连续取两次。已知第一次取到红球，"
        r"则第二次也取到红球的概率为$\underline{\quad}$；两次都取到红球的概率为$\underline{\quad}$。",
    ]),
    True, "两空填空题，空位用 \\underline{\\quad}",
)

add(
    "FB-F04", "fill_in_blank", "easy", "B2-C9-S1", "plaintext",
    "人教A必修二·随机抽样",
    "\n".join([
        "某工厂有甲、乙两个车间，现按A:B=2:3的比例从两个车间共抽取 100 名工人参加技能测试，则应从甲车间抽取__________人。",
    ]),
    True, "题干含天干「甲、乙」作车间名，以及干扰串「按A:B=2:3」",
)

add(
    "FB-F05", "fill_in_blank", "medium", "X3-C7-S5", "latex",
    "人教A选择性必修三·正态分布",
    "\n".join([
        r"设随机变量 $X\sim N(1,4)$，则 $P(-1<X<3)=$（    ）。",
        r"（参考：若 $Y\sim N(\mu,\sigma^2)$，则 $P(|Y-\mu|<\sigma)\approx0.6827$）",
    ]),
    True, "空位用半角括号内含四个空格 （    ）",
)

add(
    "FB-F06", "fill_in_blank", "easy", "X3-C6-S1", "plaintext",
    "人教A选择性必修三·分步乘法计数原理",
    "\n".join([
        "把答案填在横线上：已知集合A={1,2,3,4}，B={a,b,c}，以 A 中的元素为前项、B 中的元素为后项组成有序数对，则共可组成____个不同的有序数对。",
    ]),
    True, "「把答案填在横线上」提示语 + 干扰串「集合A、B」",
)

add(
    "FB-F07", "fill_in_blank", "hard", "X2-C4-S4", "latex",
    "人教A选择性必修二·数学归纳法",
    "\n".join([
        r"用数学归纳法证明 $1\times4+2\times7+3\times10+\cdots+n(3n+1)=n^2(n+1)$ 时，"
        r"当 $n=k+1$ 时左端应在 $n=k$ 时左端的基础上增添\underline{\qquad}。",
    ]),
    True, "空位用 \\underline{\\qquad}",
)

add(
    "FB-F08", "fill_in_blank", "medium", "X1-C3-S3-P1", "plaintext",
    "人教A选择性必修一·抛物线及其标准方程",
    "\n".join([
        "已知椭圆C：x²/16+y²/7=1 的右焦点恰好是抛物线 y²=2px 的焦点，则 p 的值为__________。",
    ]),
    True, "题干含干扰串「椭圆C：」",
)

add(
    "FB-F09", "fill_in_blank", "medium", "X3-C8-S2", "latex",
    "人教A选择性必修三·一元线性回归模型",
    "\n".join([
        r"已知一组样本数据 $(x_i,y_i)$（$i=1,2,\dots,5$）满足 $\sum x_i=15$，$\sum y_i=25$，"
        r"$\sum x_i^2=55$，$\sum x_iy_i=88$，则 $y$ 关于 $x$ 的回归直线斜率为\fillin，截距为\fillin。",
    ]),
    True, "两空填空题，空位用 \\fillin 宏",
)

add(
    "FB-F10", "fill_in_blank", "easy", "B2-C6-S4", "plaintext",
    "人教A必修二·正弦定理余弦定理（解三角形）",
    "\n".join([
        "在△ABC中，设角A、B、C所对的边依次是a、b、c，若 b²+c²-a²=bc，则角 A 的大小为__________。",
    ]),
    True, "题干含干扰串「角A、B、C所对的边」，A、B 紧跟顿号",
)

add(
    "FB-F11", "fill_in_blank", "medium", "X2-C5-S2-P2", "latex",
    "人教A选择性必修二·导数的四则运算法则",
    "\n".join([
        r"曲线 $y=\dfrac{x}{x+2}$ 在点 $\left(1,\dfrac13\right)$ 处的切线方程为\underline{\quad}。",
    ]),
    True, "空位用 \\underline{\\quad}",
)

add(
    "FB-F12", "fill_in_blank", "easy", "B1-C1-S3", "plaintext",
    "人教A必修一·集合的基本运算",
    "\n".join([
        "设全集 U={1,2,3,4,5,6}，A={1,2,4}，B={2,3,5}，则 ∁_U(A∪B) 中所含元素的个数为__________。",
    ]),
    True, "题干含干扰串 ∁_U(A∪B)",
)


# ===========================================================================
# 四、解答题 12 条
# ===========================================================================

add(
    "DA-F01", "detailed_answer", "hard", "X3-C7-S1-P2", "plaintext",
    "人教A选择性必修三·全概率公式（小问用 ①②③ 编号）",
    "\n".join([
        "甲箱中装有 3 个红球与 2 个白球，乙箱中装有 1 个红球与 4 个白球。先等可能地选一个箱子，再从选中的箱子里任取一球。",
        "① 求取到红球的概率；",
        "② 若已知取到的是红球，求该球来自甲箱的概率；",
        "③ 若把两箱球全部倒入同一个袋子，从中任取两球，求其中恰有一个红球的概率。",
    ]),
    True, "解答题小问用 ①②③ 编号 + 天干「甲箱/乙箱」作箱名",
)

add(
    "DA-F02", "detailed_answer", "hard", "X3-C6-S2-P4", "plaintext",
    "人教A选择性必修三·组合数（小问用 ①②③ 编号）",
    "\n".join([
        "甲、乙、丙、丁、戊、己六位同学站成一排合影。",
        "① 若甲必须站在最左端，共有多少种站法？",
        "② 若甲、乙两人必须相邻，共有多少种站法？",
        "③ 若甲、乙、丙三人中任意两人都不相邻，共有多少种站法？",
    ]),
    True, "解答题小问用 ①②③ 编号 + 天干「甲、乙、丙、丁、戊、己」全为人名且紧跟顿号",
)

add(
    "DA-F03", "detailed_answer", "hard", "X1-C1-S4-P2", "latex",
    "人教A选择性必修一·距离、夹角问题（小问用 enumerate+item）",
    "\n".join([
        r"如图，在直三棱柱 $ABC-A_1B_1C_1$ 中，$AB=AC=AA_1=2$，$\angle BAC=90^\circ$，$D$ 为棱 $BC$ 的中点。",
        r"\begin{enumerate}",
        r"\item 证明：$A_1D\perp BC$；",
        r"\item 求直线 $A_1B$ 与平面 $AB_1C_1$ 所成角的正弦值；",
        r"\item 求点 $A$ 到平面 $A_1BC$ 的距离。",
        r"\end{enumerate}",
    ]),
    True, "解答题小问用 \\begin{enumerate}+\\item 排版（3 个 item）",
)

add(
    "DA-F04", "detailed_answer", "hard", "X2-C5-S3-P1", "latex",
    "人教A选择性必修二·导数在研究函数中的应用（小问用 enumerate+item）",
    "\n".join([
        r"已知函数 $f(x)=\ln x+\dfrac{a}{x}$（$a$ 为实数，$x>0$）。",
        r"\begin{enumerate}",
        r"\item 当 $a=1$ 时，求曲线 $y=f(x)$ 在 $x=1$ 处的切线方程；",
        r"\item 若 $f(x)$ 在区间 $[1,+\infty)$ 上单调递增，求 $a$ 的取值范围；",
        r"\item 证明：对任意 $x>0$ 都有 $\ln x\leqslant x-1$。",
        r"\end{enumerate}",
    ]),
    True, "解答题小问用 \\begin{enumerate}+\\item 排版（3 个 item）",
)

add(
    "DA-F05", "detailed_answer", "hard", "X2-C5-S3-P1", "plaintext",
    "人教A选择性必修二·导数在研究函数中的应用（小问用半角 (1)(2)(3)）",
    "\n".join([
        "已知函数 g(x)=e^x - x - 1。",
        "(1) 求 g(x) 的最小值；",
        "(2) 证明：当 x>0 时，e^x > 1 + x + x²/2；",
        "(3) 若关于 x 的方程 e^x = mx + 1 有两个不同的实数根，求实数 m 的取值范围。",
    ]),
    True, "小问用半角括号 (1)(2)(3) 编号",
)

add(
    "DA-F06", "detailed_answer", "medium", "X3-C8-S3", "plaintext",
    "人教A选择性必修三·列联表与独立性检验（小问用半角 (1)(2)(3)）",
    "\n".join([
        "为调查某校学生每周体育锻炼时长与视力情况的关系，随机抽查 200 名学生，结果如下：锻炼时长不少于 5 小时且视力不良的有 20 人，锻炼时长不少于 5 小时且视力良好的有 60 人，锻炼时长不足 5 小时且视力不良的有 40 人，锻炼时长不足 5 小时且视力良好的有 80 人。",
        "(1) 列出 2×2 列联表并计算 χ²（保留三位小数）；",
        "(2) 依据 α=0.05 的独立性检验，能否认为该校学生每周体育锻炼时长与视力情况有关？请说明理由；",
        "(3) 若按锻炼时长分层，从视力不良的学生中抽取 6 人，再从这 6 人中任取 2 人，求恰有 1 人锻炼时长不少于 5 小时的概率。",
    ]),
    True, "小问用半角括号 (1)(2)(3) 编号；题干含 4 组人数描述",
)

add(
    "DA-F07", "detailed_answer", "medium", "B1-C1-S5-P2", "latex",
    "人教A必修一·全称量词命题和存在量词命题的否定",
    "\n".join([
        r"已知命题 $p$：“$\exists x\in[1,3]$，使得 $x^2-2x-a<0$”，命题 $q$：“$\forall x\in\mathbb{R}$，$x^2+ax+1>0$”。",
        r"（1）写出命题 $p$ 的否定 $\neg p$；",
        r"（2）若 $p$ 是真命题，求实数 $a$ 的取值范围；",
        r"（3）若 $p$ 与 $q$ 中恰有一个是真命题，求实数 $a$ 的取值范围。",
    ]),
)

add(
    "DA-F08", "detailed_answer", "hard", "X3-C7-S5", "plaintext",
    "人教A选择性必修三·正态分布（小问用半角 (1)(2)(3)）",
    "\n".join([
        "某型号零件的长度 X（单位：mm）服从正态分布 N(50,0.25)。",
        "(1) 求 P(49.5<X<50.5)；",
        "(2) 规定长度落在区间 (49,51) 内为合格品，从该批零件中随机抽取 3 件，求恰有 2 件合格品的概率；",
        "(3) 记随机抽取的 3 件中合格品件数为 Y，求 Y 的数学期望。",
    ]),
    True, "小问用半角括号 (1)(2)(3) 编号",
)

add(
    "DA-F09", "detailed_answer", "medium", "B2-C6-S4", "latex",
    "人教A必修二·正弦定理余弦定理（解三角形）",
    "\n".join([
        r"在 $\triangle ABC$ 中，内角 $A,B,C$ 的对边分别为 $a,b,c$，且 $\cos B=\dfrac35$，$b=4$。",
        r"（1）若 $a=5$，求 $\sin C$ 的值；",
        r"（2）若 $\triangle ABC$ 的面积等于 $6$，求 $a+c$ 的值。",
    ]),
    True, "题干含干扰串「内角 $A,B,C$ 的对边」，A、B、C 紧跟半角逗号",
)

add(
    "DA-F10", "detailed_answer", "hard", "X2-C4-S4", "plaintext",
    "人教A选择性必修二·数学归纳法（小问用半角 (1)(2)(3)）",
    "\n".join([
        "已知数列 {a_n} 满足 a_1=1，a_{n+1}=a_n/(1+2a_n)（n∈N*）。",
        "(1) 证明：数列 {1/a_n} 是等差数列；",
        "(2) 求数列 {a_n} 的通项公式；",
        "(3) 用数学归纳法证明：对任意 n∈N*，a_1a_2 + a_2a_3 + … + a_n a_{n+1} < 1/2。",
    ]),
    True, "小问用半角括号 (1)(2)(3) 编号；第 (3) 问要求数学归纳法",
)

add(
    "DA-F11", "detailed_answer", "hard", "X1-C3-S1-P2", "latex",
    "人教A选择性必修一·椭圆的简单几何性质",
    "\n".join([
        r"已知椭圆 $\dfrac{x^2}{4}+\dfrac{y^2}{3}=1$ 的左、右焦点分别为 $F_1$、$F_2$，"
        r"过点 $F_2$ 的直线 $l$ 与该椭圆交于 $P$、$Q$ 两点。",
        r"（1）求 $\triangle PQF_1$ 的周长；",
        r"（2）若 $\overrightarrow{PF_2}=2\overrightarrow{F_2Q}$，求直线 $l$ 的斜率。",
    ]),
)

add(
    "DA-F12", "detailed_answer", "hard", "X1-C1-S3-P2", "plaintext",
    "人教A选择性必修一·空间向量运算的坐标表示",
    "\n".join([
        "在棱长为 2 的正方体 ABCD-A₁B₁C₁D₁ 中，D、E分别为棱 CC₁、A₁D₁ 的中点。",
        "（1）建立适当的空间直角坐标系，写出点 B₁ 与点 E 的坐标；",
        "（2）求异面直线 BE 与 A₁C₁ 所成角的余弦值。",
    ]),
    True, "题干含干扰串「D、E分别为…中点」",
)


def main() -> None:
    payload = {
        "_meta": {
            "name": "第三份验收集（终验用；与 cases.json / holdout_cases.json 均不重叠）",
            "created_by": "verifier",
            "answer_key_note": "每条的期望题型/难度/章节即为本文件 form / difficulty / chapter_code 字段，不再另建答案文件。",
            "focus": [
                "多选题：6 条题干完全无多选提示语（MC-F01..F06）",
                "解答题小问编号：①②③（DA-F01/F02）、\\begin{enumerate}+\\item（DA-F03/F04）、(1)(2)(3)（DA-F05/F06/F08/F10）",
                "天干人名/箱名干扰：SC-F01、FB-F04、DA-F01、DA-F02",
                "同形干扰串：椭圆C：/角A、B、C的对边/集合A、B/∁_U(A∪B)/按A:B=2:3/交于A、B两点/D、E分别为…中点",
                "填空空位形态：\\fillin / \\underline{\\quad} / \\underline{\\qquad} / __________ / （    ） / ［　　］ / 多空 / 提示语",
            ],
        },
        "cases": CASES,
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written {OUT}  cases={len(CASES)}")
    from collections import Counter
    print("by form     :", dict(Counter(c["form"] for c in CASES)))
    print("by modality :", dict(Counter(c["modality"] for c in CASES)))
    print("tricky      :", sum(1 for c in CASES if c["tricky"]))


if __name__ == "__main__":
    main()
