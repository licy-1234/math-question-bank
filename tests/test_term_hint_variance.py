"""「方差」术语键冲突 + 统计专属词补充的回归测试。

背景：``_TERM_HINTS`` 是 ``term -> tuple[node_code, ...]`` 的加权打分词典（一个术语可以
同时给多个节点加分，``suggest_chapter_candidates`` 里 ``bump()`` 会逐个累加）。「方差」
同时属于必修二《统计》(B2-C9-S2) 和选择性必修三《随机变量及其分布》(X3-C7-S3-P2)。

缺陷 1（已修）：这两个意图被写成**同一个字典字面量里的两个同名键**，Python 后定义覆盖
先定义，``B2-C9-S2`` 这条信号被静默丢弃。
修复：合并成多值元组 ``"方差": ("B2-C9-S2", "X3-C7-S3-P2")``。

缺陷 2（已修）：即使修好缺陷 1，两边的票依然不对称 —— ``_auto_terms()`` 会从课程节点
**标题** 自动生成术语，而 ``X3-C7-S3-P2`` 的标题就叫「方差」（``B2-C9-S2`` 的标题是
「用样本估计总体」），于是选择性必修三在 ``_TERM_HINTS`` 之外又白拿一份额外加分。
后果是「方差」为唯一统计信号的短句仍会被判到 X3。
修复：补入四个**只属于必修二统计**的专属词（标准差 / 极差 / 离散程度 / 离差平方和），
给 B2-C9-S2 提供独立的语境票，把天平拉回来。

本文件锁住这两件事：

* 9 条真实感统计语料必须全部落进必修二第九章（其中一条只靠「标准差」救回来的极端短句，
  和两个、单 guilty 撤回「方差」多值就会掉到 X3 的敏感语料，单独加了用例）。
* 源码层面不许再出现同名字典键（AST 扫描，运行时 ``len(dict)`` 查不出覆盖）。

候选列表由 ``sorted(..., key=(-score, code))`` 产生，因此**排名靠前即等价于得分更高**
（并列时按 code 字典序，``B2-...`` < ``X3-...``，故严格靠前必定严格更高分）。
"""

import ast
import collections
from pathlib import Path

import pytest

from mathbank import classify_rules as cr

VARIANCE_STATS = "B2-C9-S2"
VARIANCE_PROB = "X3-C7-S3-P2"

#: 只属于必修二《统计》的专属词，用于给多义术语「方差」提供消歧语境票
STATS_ONLY_TERMS = ("标准差", "极差", "离散程度", "离差平方和")

# 候选列表可能很长，取足够大的 top_n 保证两个目标节点都在里面
TOP_N = 80

#: (用例标签, 题干, 期望的 top1 节点码)。全部含「方差」，但统计语境强弱不同。
STATS_CASES = [
    (
        "基础样例-样本平均数众数",
        "从某校高一学生中随机抽取 50 人，测得身高的样本数据如下，"
        "求这组数据的平均数、众数与方差。",
        VARIANCE_STATS,
    ),
    (
        "极端短句-仅有方差与标准差",
        "已知一组数据的方差为 4，求这组数据的标准差。",
        VARIANCE_STATS,
    ),
    (
        "中位数众数",
        "某次数学测验的成绩如下，计算这组成绩的中位数、众数与方差。",
        VARIANCE_STATS,
    ),
    (
        "平均数与稳定性",
        "甲、乙两名运动员 10 次射击成绩的平均数相同，方差分别为 2.1 和 3.4，"
        "判断谁的成绩更稳定。",
        VARIANCE_STATS,
    ),
    (
        "样本平移变换",
        "从总体中抽取容量为 n 的样本，若每个数据都加上同一个常数，"
        "则这组数据的平均数与方差如何变化？",
        VARIANCE_STATS,
    ),
    (
        "样本倍数变换",
        "已知一个样本的方差为 5，若将该样本中每个数据都乘以 2，则新样本的方差为多少？",
        VARIANCE_STATS,
    ),
    (
        "频率分布直方图",
        "根据频率分布直方图估计这组数据的平均数与方差。",
        VARIANCE_STATS,
    ),
    (
        "抽样-落在9.1",
        "采用简单随机抽样的方法抽取样本，计算所得样本数据的方差。",
        "B2-C9-S1",
    ),
    (
        "平均数与离差平方和",
        "若一组数据的平均数为 8，方差为 3，求这组数据的离差平方和。",
        VARIANCE_STATS,
    ),
]

#: 单独标记：这两条对「方差多值键」敏感 —— 只要把 方差 改回单值就会掉到选择性必修三。
#: 其余语料因为有「样本/平均数/中位数/众数/频率分布」等上下文或新补的「标准差/极差」
#: 等专属词托底，即使方差信号丢了也仍能判对，所以不适合用来证明该修复生效。
SENSITIVE_STATS_CASES = [STATS_CASES[5], STATS_CASES[7]]

PROBABILITY_STEM = "已知随机变量 X 的分布列如表所示，求 X 的数学期望与方差。"

_SOURCE_PATH = Path(cr.__file__)


def _rank_of(content: str, code: str) -> int:
    """返回 node_code 在候选排名中的下标（0 为最高分）；不在候选里返回 -1。"""
    codes = [item for item, _path in cr.suggest_chapter_candidates(content, top_n=TOP_N)]
    try:
        return codes.index(code)
    except ValueError:
        return -1


def _source_term_hint_keys() -> list[tuple[str, int]]:
    """从**源码 AST** 抽取 _TERM_HINTS 字面量里的全部键及其行号。

    运行时取值会把被覆盖的键合并掉，所以必须查源码。
    """
    tree = ast.parse(_SOURCE_PATH.read_text(encoding="utf-8"))
    pairs: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [(node.target.id, node.value)]
        elif isinstance(node, ast.Assign):
            targets = [
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            ]
        for name, value in targets:
            if name != "_TERM_HINTS" or not isinstance(value, ast.Dict):
                continue
            for key_node in value.keys:
                if key_node is None:
                    continue
                try:
                    key_value = ast.literal_eval(key_node)
                except ValueError:
                    continue
                pairs.append((key_value, getattr(key_node, "lineno", -1)))
    return pairs


def test_variance_term_declares_both_meanings():
    """「方差」必须同时指向必修二统计与选择性必修三随机变量两个节点。"""
    assert cr._TERM_HINTS["方差"] == ("B2-C9-S2", "X3-C7-S3-P2")


@pytest.mark.parametrize("term", STATS_ONLY_TERMS)
def test_statistics_only_terms_point_to_compulsory_two(term):
    """四个纯统计词必须存在且只指向必修二 9.2，用来抵消下方 X3 的节点名加成。"""
    assert term in cr._TERM_HINTS, f"统计专属词 {term} 缺失"
    assert cr._TERM_HINTS[term] == ("B2-C9-S2",)


def test_term_hints_has_no_duplicate_keys_in_source():
    """字典字面量里不允许再出现同名字典键（运行时 len() 查不出覆盖）。"""
    pairs = _source_term_hint_keys()
    assert pairs, "未能从源码解析出 _TERM_HINTS 的键"

    counts = collections.Counter(key for key, _ in pairs)
    duplicates = {
        key: [line for k, line in pairs if k == key]
        for key, count in counts.items()
        if count > 1
    }
    assert duplicates == {}, f"_TERM_HINTS 存在重复键，后者会静默覆盖前者: {duplicates}"

    # 防御内容漂移：源码里写的键数量应与运行时一致（既没被覆盖，也没漏解析）
    assert len(pairs) == len(cr._TERM_HINTS)


@pytest.mark.parametrize("content,expected_top", [c[1:] for c in STATS_CASES], ids=[c[0] for c in STATS_CASES])
def test_statistics_context_favours_compulsory_two(content, expected_top):
    """统计语境：方差节点的必修二分支必须压过选择性必修三分支。"""
    stats_rank = _rank_of(content, VARIANCE_STATS)
    prob_rank = _rank_of(content, VARIANCE_PROB)

    assert stats_rank >= 0, "必修二方差节点未进入候选列表（多值信号丢失）"
    assert prob_rank >= 0, "选择性必修三方差节点未进入候选列表"
    assert stats_rank < prob_rank, (
        f"统计语境下排名错误: {VARIANCE_STATS}#{stats_rank} 应高于 "
        f"{VARIANCE_PROB}#{prob_rank} —— {content}"
    )

    top_code = cr.suggest_chapter_candidates(content, top_n=1)[0][0]
    assert top_code == expected_top

    result = cr.classify_with_rules(content)
    assert result["chapter_code"] == expected_top
    assert result["chapter_source"] == "rule"


def test_extreme_short_stem_is_rescued_by_standard_deviation():
    """这条短句此前被判成 X3-C7-S3-P2，是补「标准差」的直接动因，单独钉住。

    题干里没有任何「样本/平均数/中位数/众数/频率分布」，只有「方差 + 标准差」；
    而 _auto_terms() 还会因为 X3-C7-S3-P2 节点名就叫「方差」再给 X3 加一次分，
    所以必须靠「标准差」这张纯统计票才能把它拉回必修二。
    """
    content = "已知一组数据的方差为 4，求这组数据的标准差。"
    assert cr.classify_with_rules(content)["chapter_code"] == VARIANCE_STATS
    assert cr.suggest_chapter_candidates(content, top_n=1)[0][0] == VARIANCE_STATS


def test_short_stem_regresses_when_standard_deviation_term_is_dropped(monkeypatch):
    """证明「标准差」这张票确实是必要的：删掉它，极端短句会重新掉回选择性必修三。"""
    content = "已知一组数据的方差为 4，求这组数据的标准差。"
    assert _rank_of(content, VARIANCE_STATS) < _rank_of(content, VARIANCE_PROB)

    monkeypatch.delitem(cr._TERM_HINTS, "标准差")
    assert _rank_of(content, VARIANCE_STATS) > _rank_of(content, VARIANCE_PROB), (
        "删掉「标准差」后排名没有变差，说明该术语已不再承担消歧职责"
    )


def test_probability_context_favours_elective_three():
    """概率语境：分布列/随机变量/数学期望累积后，选择性必修三的方差节点必须胜出。"""
    prob_rank = _rank_of(PROBABILITY_STEM, VARIANCE_PROB)
    stats_rank = _rank_of(PROBABILITY_STEM, VARIANCE_STATS)

    assert prob_rank >= 0, "选择性必修三方差节点未进入候选列表"
    # 「方差」仍应给必修二节点加分（多值声明未退化成单边覆盖），只是分数较低
    assert stats_rank >= 0, "必修二方差节点未进入候选列表（多值信号丢失）"
    assert prob_rank < stats_rank, (
        f"概率语境下排名错误: {VARIANCE_PROB}#{prob_rank} 应高于 "
        f"{VARIANCE_STATS}#{stats_rank}"
    )

    top_code = cr.suggest_chapter_candidates(PROBABILITY_STEM, top_n=1)[0][0]
    assert top_code.startswith("X3-C7")

    result = cr.classify_with_rules(PROBABILITY_STEM)
    assert result["chapter_code"].startswith("X3-C7")
    assert result["chapter_source"] == "rule"


@pytest.mark.parametrize("content", [case[1] for case in STATS_CASES] + [PROBABILITY_STEM])
def test_variance_nodes_are_scored_in_both_contexts(content):
    """两种语境下两个节点都必须拿到分，缺一个就说明某条意图又被静默丢了。"""
    codes = [code for code, _path in cr.suggest_chapter_candidates(content, top_n=TOP_N)]
    assert VARIANCE_STATS in codes
    assert VARIANCE_PROB in codes


@pytest.mark.parametrize("case", SENSITIVE_STATS_CASES, ids=[c[0] for c in SENSITIVE_STATS_CASES])
def test_sensitive_stems_regress_when_variance_key_is_collapsed(case, monkeypatch):
    """显式锁住「把方差改回单值就必须变红」的语料。

    临时把 _TERM_HINTS['方差'] 退回缺陷时的单值 X3-C7-S3-P2，验证这些统计题干会掉到
    选择性必修三；monkeypatch 结束后自动还原，既证明断言敏感，又不污染其他用例。
    """
    content = case[1]
    stats_rank = _rank_of(content, VARIANCE_STATS)
    prob_rank = _rank_of(content, VARIANCE_PROB)
    assert stats_rank < prob_rank, "修复态下这些语料应属于必修二统计"

    monkeypatch.setitem(cr._TERM_HINTS, "方差", ("X3-C7-S3-P2",))
    broken_stats_rank = _rank_of(content, VARIANCE_STATS)
    broken_prob_rank = _rank_of(content, VARIANCE_PROB)

    # 缺陷态：必修二节点要么彻底拿不到分，要么分数被选择性必修三压过
    assert broken_stats_rank == -1 or broken_stats_rank > broken_prob_rank, (
        "语料对本次修复不敏感：撤回修复后排名没有变差，需要换一条能区分的题干"
    )
