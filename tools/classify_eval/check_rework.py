#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""R1–R8 返工验收门禁。

把 team-lead 下发的返工清单写成可执行断言，终验时一键判定。
默认输出每条的 PASS/FAIL 与实测值；``--strict`` 下有任一 FAIL 则 exit 1。

用法
----
    python tools/classify_eval/check_rework.py
    python tools/classify_eval/check_rework.py --strict
    python tools/classify_eval/check_rework.py --skip-chapter   # 章节仍在调时跳过 R8
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathbank.classify_rules import (  # noqa: E402
    analyze_options,
    classify_with_rules,
    detect_subquestion_count,
    suggest_chapter_candidates,
)
from mathbank.question_types import detect_structured_question_form  # noqa: E402

FINAL = TOOLS_DIR / "final_cases.json"
HOLDOUT = TOOLS_DIR / "holdout_cases.json"

#: R2 —— 圈码/列表环境当小问编号的解答题（期望仍是解答题，不能被判成选择题）
R2_CIRCLED = {"DA-F01", "DA-F02", "DA-H08"}
R2_ENUMERATE = {"DA-F03", "DA-F04", "DA-H09"}

#: R3 —— 天干「甲乙丙丁戊」是人名的题（非选择题的绝不能被判有选项）
R3_TIANGAN_WRITTEN = {"DA-F02", "DA-H10"}
R3_TIANGAN_CHOICE = {"SC-H02", "MC-F04"}

#: R4 —— 题干完全无多选提示语的多选题（绝不能"自信地判成单选题"）
R4_NO_PROMPT_MULTI = {
    "MC-F01", "MC-F02", "MC-F03", "MC-F04", "MC-F05", "MC-F06",
    "MC-H01", "MC-H05", "MC-H09", "MC-H12",
}

#: R8 —— 过宽面层提示的探针（期望不再被拉到错误章节）
R8_PROBES = [
    ("纯英文题干（不应落到 B1-C3）",
     "Let f(x)=x^2-3x+2. Find the minimum value of f on the interval [0,3].", "B1-C3"),
    ("5000 字超长文本（不应落到 B1-C3）",
     "已知函数 f(x) 满足下列条件。" * 250, "B1-C3"),
    ("「取值集合」不应落到 B1-C1",
     r"设函数 $h(x)=\mathrm{e}^{x}-ax-1$，若 $h(x)\geqslant0$ 恒成立，求 $a$ 的取值集合。", "B1-C1"),
    (r"e^{x} 不应落到 B1-C4-S2（指数函数）",
     r"已知函数 $f(x)=x\mathrm{e}^{x}$，求 $f(x)$ 的单调区间。", "B1-C4-S2"),
]

#: evidence / review_reasons 里不允许出现的英文前缀残留（R5）
_PREFIX_RE = re.compile(r"^(macro|letters|spaced-letters|spaced-run|circled|chinese|label|phrase|scoring|blank|option)\s*[:\-]")


def load(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return [c for c in cases if isinstance(c, dict)]


class Gate:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []

    def check(self, rid: str, label: str, passed: bool, detail: str) -> None:
        self.rows.append((rid, label, bool(passed), detail))
        print(f"  {'PASS' if passed else 'FAIL'}  {rid:<4}{label}")
        print(f"        {detail}")

    @property
    def failed(self) -> list[tuple[str, str, bool, str]]:
        return [r for r in self.rows if not r[2]]


def section(title: str) -> None:
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)


def ai_payload(case: dict) -> dict:
    """模拟「模型给出理想四值」的情形（与 eval_rules 情形 A 一致）。"""
    return {
        "question_type": case["form"],
        "difficulty": case["difficulty"],
        "chapter_code": case["chapter_code"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="R1–R8 返工验收门禁")
    ap.add_argument("--strict", action="store_true", help="有 FAIL 则 exit 1")
    ap.add_argument("--skip-chapter", action="store_true", help="跳过 R8（章节仍在调时）")
    args = ap.parse_args(argv)

    gate = Gate()
    final = load(FINAL)
    holdout = load(HOLDOUT)
    all_cases = final + holdout

    # ---------------------------------------------------------------- R1
    section("R1  needs_review 必须覆盖「规则层静默覆盖模型结论」")
    override_total = 0
    disagree_silent: list[str] = []
    for case in all_cases:
        out = classify_with_rules(case["content"], ai_payload(case))
        src = out["question_type_source"]
        if src == "ai":
            continue
        override_total += 1
        # 规则层结论与模型给的不一致 -> 必须提示复核
        if out["question_type"] != case["form"] and not out["needs_review"]:
            disagree_silent.append(f"{case['id']}({case['form']}→{out['question_type']})")
    gate.check(
        "R1a", "规则层结论与模型不一致时必须提示复核", not disagree_silent,
        f"规则层定题型的条数 {override_total}/{len(all_cases)}；其中与模型不一致且未提示复核 "
        f"{len(disagree_silent)} 条" + (f"  -> {disagree_silent}" if disagree_silent else "  -> 无"),
    )

    wrong_and_silent: list[str] = []
    for case in all_cases:
        out = classify_with_rules(case["content"], ai_payload(case))
        src = out["question_type_source"]
        if src != "ai" and out["question_type"] != case["form"] and not out["needs_review"]:
            wrong_and_silent.append(f"{case['id']}({case['form']}→{out['question_type']})")
    gate.check(
        "R1b", "规则层把结论判错时不得静默", not wrong_and_silent,
        f"判错且未提示复核：{len(wrong_and_silent)} 条"
        + (f"  -> {wrong_and_silent}" if wrong_and_silent else "  -> 无"),
    )

    # ---------------------------------------------------------------- R2
    section("R2  圈码 ①②③ / \\begin{enumerate}+\\item 作小问编号时不得被判为选择题")
    bad_r2: list[str] = []
    detail_r2 = []
    for case in all_cases:
        cid = case["id"]
        if cid in R2_CIRCLED or cid in R2_ENUMERATE:
            out = classify_with_rules(case["content"], ai_payload(case))
            got = out["question_type"]
            if got != "detailed_answer":
                bad_r2.append(cid)
            detail_r2.append(f"{cid}:{got}")
    gate.check(
        "R2a", "这 6 条解答题必须仍判为解答题", not bad_r2,
        f"实测 {'、'.join(detail_r2)}" + (f"  ❌ {bad_r2}" if bad_r2 else "  -> 全部正确"),
    )

    inflated: list[str] = []
    for case in all_cases:
        cid = case["id"]
        # 圈码只作选项标记（不是小问）的题，其小问数不应被圈码污染
        if case["form"] in ("single_choice", "multi_choice") and re.search(r"[①②③④]", case["content"]):
            n = detect_subquestion_count(case["content"])
            if n >= 2:
                inflated.append(f"{cid}(小问数={n})")
    gate.check(
        "R2b", "选择题里的圈码选项不得被计成小问", not inflated,
        f"被污染的条数 {len(inflated)}" + (f"  -> {inflated}" if inflated else "  -> 无"),
    )

    weak = [
        case["id"] for case in all_cases
        if case["form"] == "detailed_answer"
        and detect_structured_question_form(case["content"]) == "choice"
        and not analyze_options(case["content"])["letters"]
    ]
    gate.check(
        "R2c", "仅靠圈码/列表宏等弱证据不得直接定 choice", not weak,
        f"弱证据定 choice 且无 A-D 字母的解答题：{len(weak)}" + (f"  -> {weak}" if weak else "  -> 无"),
    )

    # ---------------------------------------------------------------- R3
    section("R3  天干「甲、乙、丙、丁、戊」作人名时不得被当成选项标记")
    bad_r3: list[str] = []
    for case in all_cases:
        cid = case["id"]
        if cid in R3_TIANGAN_WRITTEN:
            if analyze_options(case["content"])["has_options"]:
                bad_r3.append(f"{cid}(误判有选项)")
        if cid in R3_TIANGAN_CHOICE:
            if not analyze_options(case["content"])["has_options"]:
                bad_r3.append(f"{cid}(漏判选项)")
    gate.check(
        "R3", "人名型天干不误命中、选项型天干不漏判", not bad_r3,
        f"问题条数 {len(bad_r3)}" + (f"  -> {bad_r3}" if bad_r3 else "  -> 两类均正确"),
    )

    # ---------------------------------------------------------------- R4
    section("R4  无多选提示语的多选题不得被「自信地」判成单选题")
    bad_r4: list[str] = []
    detail_r4 = []
    for case in all_cases:
        cid = case["id"]
        if cid not in R4_NO_PROMPT_MULTI:
            continue
        out = classify_with_rules(case["content"], ai_payload(case))
        got, review = out["question_type"], out["needs_review"]
        detail_r4.append(f"{cid}:{got}{'/复核' if review else ''}")
        # 允许：判对成 multi_choice；或判不出来但明确要求人工确认
        if got == "single_choice" and not review:
            bad_r4.append(cid)
    gate.check(
        "R4", "无提示语多选题：要么判 multi_choice，要么置 needs_review", not bad_r4,
        f"实测 {'、'.join(detail_r4)}" + (f"  ❌ 静默判单选：{bad_r4}" if bad_r4 else "  -> 无静默单选"),
    )

    # ---------------------------------------------------------------- R5
    section("R5  evidence 字符串必须全是可直读中文（无英文前缀残留）")
    dirty: list[str] = []
    for case in all_cases:
        out = classify_with_rules(case["content"], ai_payload(case))
        ev = out.get("evidence", {})
        buckets: list[str] = []
        buckets += list(ev.get("multi_signals", []) or [])
        buckets += list(ev.get("difficulty_signals", []) or [])
        buckets += list((ev.get("options", {}) or {}).get("evidence", []) or [])
        if isinstance(ev.get("options_evidence"), list):
            buckets += list(ev["options_evidence"])
        for text in buckets:
            if isinstance(text, str) and _PREFIX_RE.match(text.strip()):
                dirty.append(f"{case['id']}:{text}")
    gate.check(
        "R5", "evidence 无 macro:/letters:/label: 等前缀", not dirty,
        f"残留条数 {len(dirty)}" + (f"  -> {dirty[:8]}" if dirty else "  -> 无"),
    )

    # ---------------------------------------------------------------- R6
    section("R6  返回体必须包含 review_reasons")
    sample = classify_with_rules(final[0]["content"], ai_payload(final[0]))
    has_field = "review_reasons" in sample
    usable = has_field and isinstance(sample["review_reasons"], list)
    all_str = usable and all(isinstance(x, str) for x in sample["review_reasons"])
    gate.check(
        "R6a", "字段存在且为 list[str]", has_field and usable and all_str,
        f"返回体键: {sorted(sample.keys())}",
    )
    flagged = [
        c["id"] for c in all_cases
        if classify_with_rules(c["content"], ai_payload(c)).get("needs_review")
    ]
    empty_reason = []
    if has_field:
        for case in all_cases:
            out = classify_with_rules(case["content"], ai_payload(case))
            if out.get("needs_review") and not out.get("review_reasons"):
                empty_reason.append(case["id"])
    gate.check(
        "R6b", "needs_review=True 时 review_reasons 必须非空",
        has_field and not empty_reason,
        f"被标记需复核 {len(flagged)} 条；其中理由为空 {len(empty_reason)} 条"
        + (f"  -> {empty_reason[:8]}" if empty_reason else "  -> 无"),
    )

    # ---------------------------------------------------------------- R7
    section("R7  is_fallback 语义统一")
    main_py = REPO_ROOT / "main.py"
    text = main_py.read_text(encoding="utf-8") if main_py.exists() else ""
    manual = re.findall(r'^\s*\w+\["is_fallback"\]\s*=\s*True', text, re.MULTILINE)
    normal_path_true = [
        c["id"] for c in all_cases
        if classify_with_rules(c["content"], ai_payload(c)).get("is_fallback")
    ]
    gate.check(
        "R7a", "正常融合路径下 is_fallback 不为 True", not normal_path_true,
        f"正常路径被置 True 的条数 {len(normal_path_true)}"
        + (f"  -> {normal_path_true[:8]}" if normal_path_true else "  -> 无"),
    )
    gate.check(
        "R7b", "main.py 降级路径不再有第二套 is_fallback 语义", not manual,
        f"main.py 中手动置 True 的处数 {len(manual)}"
        + ("（建议改为 status='partial' 单一表达）" if manual else "  -> 无"),
    )

    # ---------------------------------------------------------------- R8
    if args.skip_chapter:
        section("R8  章节术语收紧（已跳过）")
        print("  --skip-chapter 已指定，跳过。")
    else:
        section("R8  过宽面层提示已被收紧")
        for label, content, forbidden in R8_PROBES:
            codes = [c for c, _ in suggest_chapter_candidates(content, 8)]
            hit = forbidden in codes
            gate.check(
                "R8", label, not hit,
                f"top-8 = {codes[:6] if codes else '（空）'}  禁入章节 {forbidden} "
                f"{'❌ 命中' if hit else '未命中'}",
            )
        from mathbank.classify_rules import _TERM_HINTS, _SURFACE_HINTS  # noqa: PLC0415
        from mathbank.tags import curriculum_index  # noqa: PLC0415
        covered = set()
        for _t, codes in _TERM_HINTS.items():
            covered |= set(codes)
        for _p, codes in _SURFACE_HINTS:
            covered |= set(codes)
        index = curriculum_index("A")
        holes = [c for c in index if c not in covered]
        gate.check(
            "R8z", "无术语命中的课程节点数应下降", len(holes) < 58,
            f"160 节点中无术语命中 {len(holes)} 个（返工前 58 个）",
        )

    # ---------------------------------------------------------------- 汇总
    section("汇总")
    print(f"  检查项 {len(gate.rows)} 条，通过 {len(gate.rows) - len(gate.failed)}，"
          f"未通过 {len(gate.failed)}")
    if gate.failed:
        print("  未通过项：")
        for rid, label, _ok, _detail in gate.failed:
            print(f"    - {rid}  {label}")
    else:
        print("  ✅ 全部通过")
    print("=" * 92)

    if args.strict and gate.failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
