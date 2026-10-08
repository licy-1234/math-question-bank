#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""离线评测：题型判定（Phase 2 改造后）与 Phase 1 基线的对比。

设计约束
--------
* 只用 Python 标准库，不 import fastapi / sqlalchemy / 任何第三方包，不联网；
* **评测即线上**：题型 / 难度 / 章节的判定全部通过
  ``mathbank.classify_rules.classify_with_rules`` —— 与 ``main.py`` 中
  ``/api/ai/classify`` 调用的是同一份纯函数；
* 不改动任何业务源码，产物只写入 ``tools/classify_eval/``；
* ``baseline_report.md`` 由 Phase 1 生成，本脚本**不会覆盖**它。

用法
----
    python tools/classify_eval/eval_rules.py
    python tools/classify_eval/eval_rules.py --chapter
    python tools/classify_eval/eval_rules.py --strict      # 未达门槛 exit 1
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import unicodedata
from pathlib import Path


# --------------------------------------------------------------------------
# 0. 把仓库根放进 sys.path（不依赖任何第三方包）
# --------------------------------------------------------------------------

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathbank.classify_rules import (  # noqa: E402
    analyze_options,
    classify_with_rules,
    detect_blank_slots,
    detect_multi_choice_signals,
    estimate_difficulty_prior,
    suggest_chapter_candidates,
)
from mathbank.question_types import detect_structured_question_form  # noqa: E402

TAGS_AVAILABLE = True
TAGS_ERROR = ""
try:
    from mathbank.tags import curriculum_index, node_path  # noqa: E402
except Exception as exc:  # pragma: no cover
    TAGS_AVAILABLE = False
    TAGS_ERROR = f"{type(exc).__name__}: {exc}"
    curriculum_index = None  # type: ignore[assignment]
    node_path = None  # type: ignore[assignment]


# --------------------------------------------------------------------------
# 1. 常量
# --------------------------------------------------------------------------

FORMS = ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer")
CHOICE_FORMS = ("single_choice", "multi_choice")
WRITTEN_FORMS = ("fill_in_blank", "detailed_answer")

FORM_LABEL = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题",
}

#: Phase 1 实测基线（见 tools/classify_eval/baseline_report.md，可复现）
BASELINE = {
    "structure_hit": 4,
    "options_fp": 7,
    "options_fp_base": 35,
    "options_fn": 6,
    "options_fn_base": 31,
    "blanks": None,  # Phase 1 尚无该能力
    "blanks_base": 17,
    "scenario_a_coarse": 63,
    "scenario_a_fine": 35,
    "scenario_b_coarse": 53,
    "scenario_b_fine": 30,
    "multi_distinguish": 0,
    "multi_base": 31,
    "total": 66,
}

#: 验收门槛（--strict 会据此决定退出码）
THRESHOLDS = {
    "options_fp_max": 2,
    "options_fn_max": 2,
    # ``cases.json`` 设计时有 17 条填空题，门槛写作 15/17；改成比例后换用别的
    # 用例集（如留出集）也能直接复用同一套门槛，不会因样本数不同误报。
    "blanks_rate_min": 15 / 17,
    "structure_rate_min": 0.40,
    "scenario_a_fine_min": 0.90,
    "scenario_b_fine_min": 0.85,
    "multi_distinguish_min": 0.90,
}

NOISE_CANDIDATES = {
    "single_choice": ["fill_in_blank", "detailed_answer"],
    "multi_choice": ["fill_in_blank", "detailed_answer"],
    "fill_in_blank": ["choice", "choice", "detailed_answer"],
    "detailed_answer": ["choice", "choice", "fill_in_blank"],
}


# --------------------------------------------------------------------------
# 2. 用例装载与噪声
# --------------------------------------------------------------------------

def load_cases(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return [case for case in cases if isinstance(case, dict)]


def build_noise_map(cases: list[dict], rate: float, seed: int) -> dict[str, object]:
    """与 Phase 1 完全相同的确定性噪声，保证前后可比。"""

    rng = random.Random(seed)
    total = len(cases)
    count = int(round(total * rate))
    picked = set(rng.sample(range(total), min(count, total)))
    noise: dict[str, object] = {}
    for index, case in enumerate(cases):
        if index in picked:
            noise[str(case.get("id"))] = rng.choice(
                NOISE_CANDIDATES.get(str(case.get("form")), ["unknown"])
            )
    return noise


# --------------------------------------------------------------------------
# 3. 评测
# --------------------------------------------------------------------------

def rule_layer_rows(cases: list[dict]) -> list[dict]:
    rows = []
    for case in cases:
        content = str(case.get("content", ""))
        form = str(case.get("form", ""))
        options = analyze_options(content)
        rows.append(
            {
                "id": str(case.get("id", "")),
                "form": form,
                "modality": str(case.get("modality", "")),
                "difficulty": str(case.get("difficulty", "")),
                "chapter_code": str(case.get("chapter_code", "")),
                "tricky": bool(case.get("tricky", False)),
                "tricky_reason": str(case.get("tricky_reason", "")),
                "options": options,
                "has_options": options["has_options"],
                "blanks": detect_blank_slots(content, has_options=options["has_options"]),
                "multi": detect_multi_choice_signals(content),
                "structure": detect_structured_question_form(content),
            }
        )
    return rows


def run_scenario(cases: list[dict], noise: dict[str, object] | None = None) -> list[dict]:
    rows = []
    for case in cases:
        case_id = str(case.get("id", ""))
        expected = str(case.get("form", ""))
        payload = {
            "question_type": expected,
            "difficulty": str(case.get("difficulty", "")),
            "chapter_code": str(case.get("chapter_code", "")),
        }
        noisy = False
        if noise and case_id in noise:
            payload["question_type"] = noise[case_id]
            noisy = True
        fused = classify_with_rules(str(case.get("content", "")), payload)
        rows.append(
            {
                "id": case_id,
                "form": expected,
                "modality": str(case.get("modality", "")),
                "chapter_code": str(case.get("chapter_code", "")),
                "expected": expected,
                "predicted": fused["question_type"],
                "source": fused["question_type_source"],
                "coarse": fused["question_form"],
                "noisy": noisy,
                "ok": fused["question_type"] == expected,
                "ok_coarse": fused["question_form"] == (
                    "choice" if expected in CHOICE_FORMS else expected
                ),
                "fused": fused,
            }
        )
    return rows


def summarize(rows: list[dict], key: str = "ok") -> dict:
    total = len(rows)
    by_form = {}
    for form in FORMS:
        subset = [r for r in rows if r["form"] == form]
        if not subset:
            continue
        good = sum(1 for r in subset if r[key])
        by_form[form] = {
            "n": len(subset),
            "ok": good,
            "rate": good / len(subset),
            "wrong": [r for r in subset if not r[key]],
        }
    good = sum(1 for r in rows if r[key])
    return {
        "total": total,
        "ok": good,
        "rate": good / total if total else 0.0,
        "by_form": by_form,
        "wrong": [r for r in rows if not r[key]],
    }


def chapter_metrics(cases: list[dict], top_n: int = 8) -> dict:
    if not TAGS_AVAILABLE:
        return {}
    top1 = top3 = top8 = branch = 0
    details = []
    for case in cases:
        expected = str(case.get("chapter_code", ""))
        codes = [code for code, _ in suggest_chapter_candidates(str(case.get("content", "")), top_n)]
        hit1 = bool(codes) and codes[0] == expected
        hit3 = expected in codes[:3]
        hit8 = expected in codes[:8]
        same_branch = hit1 or (
            bool(codes)
            and (expected.startswith(codes[0] + "-") or codes[0].startswith(expected + "-"))
        )
        top1 += hit1
        top3 += hit3
        top8 += hit8
        branch += same_branch
        details.append(
            {"id": case.get("id"), "expected": expected, "top": codes[:3], "hit8": hit8}
        )
    total = len(cases)
    return {
        "total": total,
        "top1": top1,
        "top3": top3,
        "top8": top8,
        "branch": branch,
        "details": details,
    }


def difficulty_metrics(cases: list[dict]) -> dict:
    levels = {"easy": 0, "medium": 1, "hard": 2}
    within = exact = off2 = 0
    bad = []
    for case in cases:
        expected = str(case.get("difficulty", ""))
        prior, signals = estimate_difficulty_prior(str(case.get("content", "")))
        delta = abs(levels.get(prior, 1) - levels.get(expected, 1))
        if delta == 0:
            exact += 1
        if delta <= 1:
            within += 1
        else:
            off2 += 1
            bad.append({"id": case.get("id"), "expected": expected, "prior": prior})
    total = len(cases)
    return {
        "total": total,
        "exact": exact,
        "within1": within,
        "off2": off2,
        "bad": bad,
    }


# --------------------------------------------------------------------------
# 4. 输出
# --------------------------------------------------------------------------

def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [_width(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _width(str(cell)))
    print("  " + "  ".join(_pad(h, widths[i]) for i, h in enumerate(headers)))
    print("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        print("  " + "  ".join(_pad(str(c), widths[i]) for i, c in enumerate(row)))


def pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _cell(text: str, limit: int = 60) -> str:
    text = str(text).replace("|", "/").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --------------------------------------------------------------------------
# 5. 报告
# --------------------------------------------------------------------------

def render_report(
    cases: list[dict],
    rule_rows: list[dict],
    stats_a: dict,
    stats_b: dict,
    rows_b: list[dict],
    noise: dict[str, object],
    noise_rate: float,
    seed: int,
    chapter: dict,
    difficulty: dict,
    chapter_detail: bool,
    cases_path: Path,
    checks: list[tuple[str, bool, str]],
) -> str:
    out: list[str] = []
    add = out.append
    total = len(cases)

    add("# 题型判定改造后评测报告（Phase 2）")
    add("")
    add(f"- 用例集：`{cases_path.as_posix()}`（{total} 条）")
    add(f"- 判定入口：`mathbank.classify_rules.classify_with_rules`（与 `main.py:/api/ai/classify` 同一份代码）")
    add(f"- 噪声参数：rate={noise_rate}，seed={seed}，注入 {len(noise)} 条（与 Phase 1 完全一致，保证可比）")
    add(f"- 基线数据：`tools/classify_eval/baseline_report.md`（Phase 1 实测）")
    add(f"- 章节树：{'可用' if TAGS_AVAILABLE else '不可用 -> ' + TAGS_ERROR}")
    add("")

    # ---- 1. 验收门槛一览 ----
    add("## 1. 验收门槛达成情况")
    add("")
    add("| # | 指标 | 门槛 | Baseline (Phase 1) | After (Phase 2) | 结论 |")
    add("| --- | --- | --- | --- | --- | --- |")
    for name, passed, detail in checks:
        parts = detail.split("|")
        add(f"| {name} | {parts[0]} | {parts[1]} | {parts[2]} | {parts[3]} | "
            f"{'✅ 达标' if passed else '❌ 未达标'} |")
    add("")

    # ---- 2. 规则层 ----
    written = [r for r in rule_rows if r["form"] in WRITTEN_FORMS]
    choice = [r for r in rule_rows if r["form"] in CHOICE_FORMS]
    fp = [r for r in written if r["has_options"]]
    fn = [r for r in choice if not r["has_options"]]
    fills = [r for r in rule_rows if r["form"] == "fill_in_blank"]
    blanks_hit = [r for r in fills if r["blanks"]]
    struct_hit = [r for r in rule_rows if r["structure"]]

    add("## 2. 规则层能力（改造后）")
    add("")
    add("| 指标 | Baseline | After | 说明 |")
    add("| --- | --- | --- | --- |")
    add(f"| `detect_choice_options` 误判 FP（填空/解答题判有选项） | "
        f"{BASELINE['options_fp']}/{BASELINE['options_fp_base']} = 20.0% | "
        f"**{len(fp)}/{len(written)} = {pct(len(fp) / len(written) if written else 0)}** | "
        f"门槛 ≤ {THRESHOLDS['options_fp_max']} |")
    add(f"| `detect_choice_options` 漏判 FN（单选/多选题判无选项） | "
        f"{BASELINE['options_fn']}/{BASELINE['options_fn_base']} = 19.4% | "
        f"**{len(fn)}/{len(choice)} = {pct(len(fn) / len(choice) if choice else 0)}** | "
        f"门槛 ≤ {THRESHOLDS['options_fn_max']} |")
    add(f"| `detect_blank_slots` 填空题命中 | 无此能力 | "
        f"**{len(blanks_hit)}/{len(fills)} = {pct(len(blanks_hit) / len(fills) if fills else 0)}** | "
        f"门槛 ≥ {pct(THRESHOLDS['blanks_rate_min'])} |")
    add(f"| `detect_structured_question_form` 命中率 | "
        f"{BASELINE['structure_hit']}/{BASELINE['total']} = 6.1% | "
        f"**{len(struct_hit)}/{total} = {pct(len(struct_hit) / total if total else 0)}** | "
        f"门槛 ≥ {int(THRESHOLDS['structure_rate_min'] * 100)}% |")
    add("")

    add("### 2.1 按题型拆分")
    add("")
    add("| 题型 | 条数 | 结构命中 | 判有选项 | FP | FN |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for form in FORMS:
        subset = [r for r in rule_rows if r["form"] == form]
        if not subset:
            continue
        add(f"| {FORM_LABEL[form]} | {len(subset)} | "
            f"{sum(1 for r in subset if r['structure'])} | "
            f"{sum(1 for r in subset if r['has_options'])} | "
            f"{sum(1 for r in subset if r['has_options'] and form in WRITTEN_FORMS)} | "
            f"{sum(1 for r in subset if not r['has_options'] and form in CHOICE_FORMS)} |")
    add("")

    if fp:
        add("### 2.2 仍然误判为「有选项」的填空/解答题")
        add("")
        add("| 用例ID | 题型 | 命中证据 |")
        add("| --- | --- | --- |")
        for row in fp:
            add(f"| `{row['id']}` | {FORM_LABEL.get(row['form'], row['form'])} | "
                f"`{_cell(', '.join(row['options']['evidence']))}` |")
        add("")
    if fn:
        add("### 2.3 仍然漏判选项的单选/多选题")
        add("")
        add("| 用例ID | 题型 | 命中证据 |")
        add("| --- | --- | --- |")
        for row in fn:
            add(f"| `{row['id']}` | {FORM_LABEL.get(row['form'], row['form'])} | "
                f"`{_cell(', '.join(row['options']['evidence']) or '无')}` |")
        add("")

    # ---- 3. 题型决策 ----
    add("## 3. 题型决策准确率（细粒度四值）")
    add("")
    add("| 题型 | 条数 | 情形A正确 | 情形A准确率 | 情形B正确 | 情形B准确率 |")
    add("| --- | ---: | ---: | ---: | ---: | ---: |")
    for form in FORMS:
        stat_a = stats_a["by_form"].get(form)
        stat_b = stats_b["by_form"].get(form)
        if not stat_a:
            continue
        add(f"| {FORM_LABEL[form]} | {stat_a['n']} | {stat_a['ok']} | {pct(stat_a['rate'])} | "
            f"{stat_b['ok']} | {pct(stat_b['rate'])} |")
    add(f"| **合计** | **{total}** | **{stats_a['ok']}** | **{pct(stats_a['rate'])}** | "
        f"**{stats_b['ok']}** | **{pct(stats_b['rate'])}** |")
    add("")
    add(f"- 情形 A（AI 给期望四值）：baseline 细粒度 "
        f"{BASELINE['scenario_a_fine']}/{BASELINE['total']} = 53.0%，粗粒度 "
        f"{BASELINE['scenario_a_coarse']}/{BASELINE['total']} = 95.5%；"
        f"改造后细粒度 **{stats_a['ok']}/{total} = {pct(stats_a['rate'])}**")
    add(f"- 情形 B（20% 噪声）：baseline 细粒度 "
        f"{BASELINE['scenario_b_fine']}/{BASELINE['total']} = 45.5%，粗粒度 "
        f"{BASELINE['scenario_b_coarse']}/{BASELINE['total']} = 80.3%；"
        f"改造后细粒度 **{stats_b['ok']}/{total} = {pct(stats_b['rate'])}**")
    choice_rows_a = [r for r in rows_b if r["form"] in CHOICE_FORMS]
    multi_ok = sum(1 for r in choice_rows_a if r["ok"])
    add(f"- 单选 vs 多选取分（情形 A）：baseline "
        f"{BASELINE['multi_distinguish']}/{BASELINE['multi_base']} = 0.0%（`normalize_ai_question_form` 折叠）；"
        f"改造后 **{multi_ok}/{len(choice_rows_a)} = "
        f"{pct(multi_ok / len(choice_rows_a) if choice_rows_a else 0)}**")
    add("")

    # ---- 4. 剩余错误 ----
    add("## 4. 仍然判错的用例")
    add("")
    wrong_a = stats_a["wrong"]
    wrong_b = stats_b["wrong"]
    if not wrong_a and not wrong_b:
        add("情形 A 与情形 B 均无判错用例。")
        add("")
    else:
        if wrong_a:
            add("### 4.1 情形 A 判错")
            add("")
            add("| 用例ID | 形态 | 期望 | 预测 | 判定来源 | 刁钻点 |")
            add("| --- | --- | --- | --- | --- | --- |")
            for row in wrong_a:
                add(f"| `{row['id']}` | {row['modality']} | {FORM_LABEL.get(row['form'], row['form'])} | "
                    f"`{row['predicted']}` | {row['source']} | {_cell(row.get('tricky_reason') or '—', 50)} |")
            add("")
        if wrong_b:
            add("### 4.2 情形 B 判错")
            add("")
            add("| 用例ID | 形态 | 期望 | 预测 | 判定来源 | 是否被注入噪声 |")
            add("| --- | --- | --- | --- | --- | --- |")
            for row in wrong_b:
                add(f"| `{row['id']}` | {row['modality']} | {FORM_LABEL.get(row['form'], row['form'])} | "
                    f"`{row['predicted']}` | {row['source']} | {'是' if row['noisy'] else '否'} |")
            add("")

    # ---- 5. 噪声救援 ----
    noisy_rows = [r for r in rows_b if r["noisy"]]
    saved = [r for r in noisy_rows if r["ok"]]
    add("## 5. 噪声用例的纠偏效果")
    add("")
    add(f"- 注入噪声 {len(noisy_rows)} 条，最终仍判定正确 **{len(saved)}** 条"
        f"（{pct(len(saved) / len(noisy_rows) if noisy_rows else 0)}）")
    add(f"- 按纠偏来源：structure "
        f"{sum(1 for r in saved if r['source'] == 'structure')}、corrected "
        f"{sum(1 for r in saved if r['source'] == 'corrected')}、ai "
        f"{sum(1 for r in saved if r['source'] == 'ai')}、rule "
        f"{sum(1 for r in saved if r['source'] == 'rule')}")
    add("")
    add("| 用例ID | 题型 | 期望 | AI错误输出 | 最终预测 | 来源 | 结果 |")
    add("| --- | --- | --- | --- | --- | --- | --- |")
    for row in noisy_rows:
        add(f"| `{row['id']}` | {FORM_LABEL.get(row['form'], row['form'])} | `{row['expected']}` | "
            f"`{noise.get(row['id'])}` | `{row['predicted']}` | {row['source']} | "
            f"{'✅ 救回' if row['ok'] else '❌ 错'} |")
    add("")

    # ---- 6. 章节 ----
    add("## 6. 章节候选检索（参考指标，如实上报）")
    add("")
    if not chapter:
        add("> 章节树不可用，本节跳过。")
        add("")
    else:
        add("| 指标 | 命中 / 总数 | 命中率 |")
        add("| --- | --- | ---: |")
        add(f"| top-1 精确命中 | {chapter['top1']}/{chapter['total']} | "
            f"{pct(chapter['top1'] / chapter['total'])} |")
        add(f"| top-3 命中 | {chapter['top3']}/{chapter['total']} | "
            f"{pct(chapter['top3'] / chapter['total'])} |")
        add(f"| top-8 命中 | {chapter['top8']}/{chapter['total']} | "
            f"{pct(chapter['top8'] / chapter['total'])} |")
        add(f"| top-1 同分支（父/子也算可用） | {chapter['branch']}/{chapter['total']} | "
            f"{pct(chapter['branch'] / chapter['total'])} |")
        add("")
        add("> 说明：这是**关键词检索**的召回能力，最终章节仍由模型从候选列表中挑选。"
            "top-1 偏低的主要原因是 66 条标注中有 32 条标到小节级，而题面往往只出现"
            "「复数」「对数」这类章/节级词，无法区分同级小节。详见 §8 风险。")
        add("")
        if chapter_detail:
            add("| 用例ID | 期望 code | top-3 候选 | top-8 是否命中 |")
            add("| --- | --- | --- | --- |")
            for item in chapter["details"]:
                add(f"| `{item['id']}` | `{item['expected']}` | "
                    f"{', '.join('`' + c + '`' for c in item['top']) or '（空）'} | "
                    f"{'✅' if item['hit8'] else '❌'} |")
            add("")

    # ---- 7. 难度 ----
    add("## 7. 难度先验（参考指标，如实上报）")
    add("")
    add("| 指标 | 命中 / 总数 | 命中率 |")
    add("| --- | --- | ---: |")
    add(f"| 与标注完全一致 | {difficulty['exact']}/{difficulty['total']} | "
        f"{pct(difficulty['exact'] / difficulty['total'])} |")
    add(f"| ±1 档以内 | {difficulty['within1']}/{difficulty['total']} | "
        f"{pct(difficulty['within1'] / difficulty['total'])} |")
    add(f"| 相差 2 档（easy↔hard） | {difficulty['off2']}/{difficulty['total']} | "
        f"{pct(difficulty['off2'] / difficulty['total'])} |")
    add("")
    if difficulty["bad"]:
        add("| 用例ID | 标注 | 先验 |")
        add("| --- | --- | --- |")
        for item in difficulty["bad"]:
            add(f"| `{item['id']}` | {item['expected']} | {item['prior']} |")
        add("")

    # ---- 8. 风险 ----
    add("## 8. 仍未解决的风险")
    add("")
    add("1. **章节 top-1 只有 "
        f"{pct(chapter['top1'] / chapter['total']) if chapter else 'n/a'}**：关键词检索无法"
        "区分同级小节（如「复数」命中章 B2-C7 而非节 B2-C7-S2）。线上最终由模型在候选列表里选，"
        "且 `chapter_source='rule'` 时会置 `needs_review=True` 提示老师核对，但不要指望规则层单独定准章节。")
    add("2. **难度先验只保证 ±1 档**：它是结构先验（小问数/含参讨论/设问词），"
        "读不出「计算量大」「技巧隐蔽」这类真实难度来源；模型值与先验相差两档时只标记冲突、不覆盖。")
    add("3. **术语表与阈值是拿这 66 条调出来的**，存在过拟合风险；换成真实题库后各项数字大概率下降，"
        "建议用真实数据重跑本脚本复核。")
    add("4. **多选题判据依赖提示语**：若试卷只在 section 标题写「多项选择题」而不在题干里，"
        "`detect_multi_choice_signals` 会漏（本用例集不覆盖该形态）。")
    add("5. **`main.py` 本次无法在本机运行验证**：当前解释器未安装 requests/fastapi，"
        "只做了 `py_compile` 与静态名称解析检查，端点行为需在有依赖的环境里冒烟一次。")
    add("")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------
# 6. 主流程
# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="改造后题型判定离线评测")
    parser.add_argument("--cases", default=str(TOOLS_DIR / "cases.json"))
    parser.add_argument("--out", default=str(TOOLS_DIR / "improved_report.md"))
    parser.add_argument("--chapter", action="store_true", help="输出章节候选逐条明细")
    parser.add_argument("--noise-rate", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260)
    parser.add_argument("--strict", action="store_true", help="未达验收门槛时 exit 1")
    args = parser.parse_args(argv)

    cases_path = Path(args.cases)
    if not cases_path.is_absolute():
        cases_path = (Path.cwd() / cases_path).resolve()
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = (Path.cwd() / out_path).resolve()

    cases = load_cases(cases_path)
    total = len(cases)

    print("=" * 80)
    print("题型判定改造后离线评测（Phase 2）")
    print("=" * 80)
    print(f"仓库根  : {REPO_ROOT}")
    print(f"用例集  : {cases_path}  （{total} 条）")
    print(f"判定入口: mathbank.classify_rules.classify_with_rules")
    print()

    rule_rows = rule_layer_rows(cases)
    written = [r for r in rule_rows if r["form"] in WRITTEN_FORMS]
    choice = [r for r in rule_rows if r["form"] in CHOICE_FORMS]
    fp = [r for r in written if r["has_options"]]
    fn = [r for r in choice if not r["has_options"]]
    fills = [r for r in rule_rows if r["form"] == "fill_in_blank"]
    blanks_hit = sum(1 for r in fills if r["blanks"])
    struct_hit = sum(1 for r in rule_rows if r["structure"])

    print("[规则层]")
    print_table(
        ["指标", "Baseline", "After", "门槛"],
        [
            ["选项误判 FP", f"{BASELINE['options_fp']}/{BASELINE['options_fp_base']}",
             f"{len(fp)}/{len(written)}", f"≤ {THRESHOLDS['options_fp_max']}"],
            ["选项漏判 FN", f"{BASELINE['options_fn']}/{BASELINE['options_fn_base']}",
             f"{len(fn)}/{len(choice)}", f"≤ {THRESHOLDS['options_fn_max']}"],
            ["填空位命中", "无", f"{blanks_hit}/{len(fills)}",
             f"≥ {pct(THRESHOLDS['blanks_rate_min'])}"],
            ["结构命中率", "4/66 (6.1%)", f"{struct_hit}/{total} ({pct(struct_hit / total)})",
             f"≥ {int(THRESHOLDS['structure_rate_min'] * 100)}%"],
        ],
    )
    print()

    rows_a = run_scenario(cases)
    stats_a = summarize(rows_a)
    noise = build_noise_map(cases, args.noise_rate, args.seed)
    rows_b = run_scenario(cases, noise)
    stats_b = summarize(rows_b)

    print("[题型决策 · 细粒度四值]")
    print_table(
        ["题型", "条数", "情形A", "准确率", "情形B", "准确率"],
        [
            [FORM_LABEL[form], str(stats_a["by_form"][form]["n"]),
             str(stats_a["by_form"][form]["ok"]), pct(stats_a["by_form"][form]["rate"]),
             str(stats_b["by_form"][form]["ok"]), pct(stats_b["by_form"][form]["rate"])]
            for form in FORMS if form in stats_a["by_form"]
        ],
    )
    print(f"  合计 情形A {stats_a['ok']}/{total} = {pct(stats_a['rate'])}   "
          f"情形B {stats_b['ok']}/{total} = {pct(stats_b['rate'])}")
    choice_rows_a = [r for r in rows_a if r["form"] in CHOICE_FORMS]
    multi_ok = sum(1 for r in choice_rows_a if r["ok"])
    print(f"  单选/多选取分（情形A）: {multi_ok}/{len(choice_rows_a)} = "
          f"{pct(multi_ok / len(choice_rows_a) if choice_rows_a else 0)}  (baseline 0/31 = 0.0%)")
    print()

    chapter = chapter_metrics(cases) if args.chapter or True else {}
    difficulty = difficulty_metrics(cases)
    if chapter:
        print("[章节候选]")
        print(f"  top-1 {chapter['top1']}/{total} = {pct(chapter['top1'] / total)}   "
              f"top-3 {chapter['top3']}/{total} = {pct(chapter['top3'] / total)}   "
              f"top-8 {chapter['top8']}/{total} = {pct(chapter['top8'] / total)}")
    print(f"[难度先验] ±1 档 {difficulty['within1']}/{total} = "
          f"{pct(difficulty['within1'] / total)}   完全一致 {difficulty['exact']}/{total}")
    print()

    all_pass = True
    checks: list[tuple[str, bool, str]] = []

    def register(name: str, label: str, passed: bool, threshold: str, base: str, after: str) -> None:
        nonlocal all_pass
        all_pass = all_pass and passed
        checks.append((name, passed, f"{label}|{threshold}|{base}|{after}"))

    register("1a", "选项误判 FP（填空/解答题）", len(fp) <= THRESHOLDS["options_fp_max"], "≤2/35",
             f"{BASELINE['options_fp']}/{BASELINE['options_fp_base']}", f"{len(fp)}/{len(written)}")
    register("1b", "选项漏判 FN（单选/多选题）", len(fn) <= THRESHOLDS["options_fn_max"], "≤2/31",
             f"{BASELINE['options_fn']}/{BASELINE['options_fn_base']}", f"{len(fn)}/{len(choice)}")
    blanks_rate = (blanks_hit / len(fills)) if fills else 0.0
    register("2", "`detect_blank_slots` 填空命中",
             blanks_rate >= THRESHOLDS["blanks_rate_min"], f"≥{pct(THRESHOLDS['blanks_rate_min'])}",
             "无此能力", f"{blanks_hit}/{len(fills)} = {pct(blanks_rate)}")
    register("3", "`detect_structured_question_form` 命中率",
             struct_hit / total >= THRESHOLDS["structure_rate_min"], "≥40%", "4/66 (6.1%)",
             f"{struct_hit}/{total} ({pct(struct_hit / total)})")
    register("4a", "细粒度准确率·情形A（AI 理想）",
             stats_a["rate"] >= THRESHOLDS["scenario_a_fine_min"], "≥90%",
             f"{BASELINE['scenario_a_fine']}/{total} (53.0%)",
             f"{stats_a['ok']}/{total} ({pct(stats_a['rate'])})")
    register("4b", "细粒度准确率·情形B（20% 噪声）",
             stats_b["rate"] >= THRESHOLDS["scenario_b_fine_min"], "≥85%",
             f"{BASELINE['scenario_b_fine']}/{total} (45.5%)",
             f"{stats_b['ok']}/{total} ({pct(stats_b['rate'])})")
    register("5", "单选 vs 多选取分（情形A）",
             multi_ok / len(choice_rows_a) >= THRESHOLDS["multi_distinguish_min"], "≥90%",
             "0/31 (0.0%)", f"{multi_ok}/{len(choice_rows_a)} "
             f"({pct(multi_ok / len(choice_rows_a))})")

    print("[验收门槛]")
    for name, passed, detail in checks:
        parts = detail.split("|")
        print(f"  {'✅' if passed else '❌'} {parts[0]:<4} 门槛 {parts[1]:<8} "
              f"baseline {parts[2]:<18} after {parts[3]}")
    print()

    report = render_report(
        cases=cases,
        rule_rows=rule_rows,
        stats_a=stats_a,
        stats_b=stats_b,
        rows_b=rows_b,
        noise=noise,
        noise_rate=args.noise_rate,
        seed=args.seed,
        chapter=chapter,
        difficulty=difficulty,
        chapter_detail=args.chapter,
        cases_path=cases_path,
        checks=checks,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    print(f"报告已写入: {out_path}")
    print("=" * 80)

    if args.strict and not all_pass:
        print("存在未达标指标，--strict 生效，退出码 1")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
