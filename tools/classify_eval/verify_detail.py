#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立验证：逐条列出留出集上的判错用例 + 章节/难度/规则层明细。

只读业务源码，产物写 tools/classify_eval/。
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathbank.classify_rules import (  # noqa: E402
    analyze_options,
    classify_with_rules,
    detect_blank_slots,
    detect_subquestion_count,
    estimate_difficulty_prior,
    multi_choice_signals,
    suggest_chapter_candidates,
)
from mathbank.question_types import detect_structured_question_form  # noqa: E402

FORM_LABEL = {
    "single_choice": "单选",
    "multi_choice": "多选",
    "fill_in_blank": "填空",
    "detailed_answer": "解答",
}
CHOICE_FORMS = ("single_choice", "multi_choice")
WRITTEN_FORMS = ("fill_in_blank", "detailed_answer")
NOISE_CANDIDATES = {
    "single_choice": ["fill_in_blank", "detailed_answer"],
    "multi_choice": ["fill_in_blank", "detailed_answer"],
    "fill_in_blank": ["choice", "choice", "detailed_answer"],
    "detailed_answer": ["choice", "choice", "fill_in_blank"],
}


def load_cases(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return [c for c in cases if isinstance(c, dict)]


def build_noise(cases, rate, seed):
    rng = random.Random(seed)
    total = len(cases)
    picked = set(rng.sample(range(total), min(int(round(total * rate)), total)))
    out = {}
    for i, case in enumerate(cases):
        if i in picked:
            out[str(case.get("id"))] = rng.choice(
                NOISE_CANDIDATES.get(str(case.get("form")), ["unknown"])
            )
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=str(TOOLS_DIR / "holdout_cases.json"))
    ap.add_argument("--noise-rate", type=float, default=0.2)
    ap.add_argument("--seed", type=int, default=20260)
    args = ap.parse_args(argv)

    cases = load_cases(Path(args.cases))
    noise = build_noise(cases, args.noise_rate, args.seed)

    print("=" * 100)
    print("逐条明细：规则层证据 / AI 理想情形（A）/ 20% 噪声情形（B）")
    print("=" * 100)
    header = f"{'ID':<8}{'期望':<6}{'A预测':<8}{'B预测':<8}{'结构':<8}{'选项':<6}{'空位':<6}{'多选':<6}{'小问':<4}"
    print(header)
    print("-" * 100)

    rows = []
    for case in cases:
        cid = str(case.get("id"))
        expected = str(case.get("form"))
        content = str(case.get("content", ""))
        opts = analyze_options(content)
        blanks = detect_blank_slots(content, has_options=opts["has_options"])
        struct = detect_structured_question_form(content)
        multi = bool(multi_choice_signals(content))
        subq = detect_subquestion_count(content)

        payload_a = {
            "question_type": expected,
            "difficulty": str(case.get("difficulty", "")),
            "chapter_code": str(case.get("chapter_code", "")),
        }
        fused_a = classify_with_rules(content, payload_a)

        payload_b = dict(payload_a)
        noisy = cid in noise
        if noisy:
            payload_b["question_type"] = noise[cid]
        fused_b = classify_with_rules(content, payload_b)

        rows.append(
            {
                "id": cid,
                "expected": expected,
                "pred_a": fused_a["question_type"],
                "pred_b": fused_b["question_type"],
                "src_a": fused_a["question_type_source"],
                "src_b": fused_b["question_type_source"],
                "struct": struct,
                "options": opts,
                "blanks": blanks,
                "multi": multi,
                "subq": subq,
                "noisy": noisy,
                "noise": noise.get(cid),
                "ok_a": fused_a["question_type"] == expected,
                "ok_b": fused_b["question_type"] == expected,
                "case": case,
            }
        )
        print(
            f"{cid:<8}{FORM_LABEL[expected]:<6}"
            f"{('OK' if rows[-1]['ok_a'] else FORM_LABEL.get(fused_a['question_type'], fused_a['question_type'])):<8}"
            f"{('OK' if rows[-1]['ok_b'] else FORM_LABEL.get(fused_b['question_type'], fused_b['question_type'])):<8}"
            f"{str(struct):<8}{str(opts['has_options']):<6}{str(blanks):<6}{str(multi):<6}{subq:<4}"
        )

    # ---- 错误明细 ----
    print()
    print("=" * 100)
    print("判错用例逐条分析")
    print("=" * 100)
    for key, label in (("ok_a", "情形A（AI 给期望四值）"), ("ok_b", "情形B（20% 噪声）")):
        bad = [r for r in rows if not r[key]]
        print()
        print(f"### {label}：{len(rows) - len(bad)}/{len(rows)} 正确，判错 {len(bad)} 条")
        for r in bad:
            opts = r["options"]
            print()
            print(f"  [{r['id']}] {r['case'].get('source_hint','')}")
            print(f"      期望   : {r['expected']}（{FORM_LABEL[r['expected']]}）")
            print(f"      实际   : {r['pred_a'] if key=='ok_a' else r['pred_b']}"
                  f"  source={r['src_a'] if key=='ok_a' else r['src_b']}"
                  f"  噪声注入={r['noise'] if r['noisy'] else '否'}")
            print(f"      结构层 : {r['struct']}   空位={r['blanks']}   多选信号={r['multi']}   小问数={r['subq']}")
            print(f"      选项证据: {opts['evidence']}")
            print(f"      has_options={opts['has_options']} letters={opts['letters']} styles={opts['styles']}")
            print(f"      题干    : {r['case']['content'][:150]!r}")

    # ---- 规则层 FP / FN ----
    print()
    print("=" * 100)
    print("规则层指标（独立于 AI）")
    print("=" * 100)
    written = [r for r in rows if r["expected"] in WRITTEN_FORMS]
    choice = [r for r in rows if r["expected"] in CHOICE_FORMS]
    fp = [r for r in written if r["options"]["has_options"]]
    fn = [r for r in choice if not r["options"]["has_options"]]
    fills = [r for r in rows if r["expected"] == "fill_in_blank"]
    fill_hit = [r for r in fills if r["blanks"]]
    print(f"  选项误判 FP（填空/解答题被判有选项）: {len(fp)}/{len(written)}  -> "
          f"{', '.join(r['id'] for r in fp) or '无'}")
    print(f"  选项漏判 FN（单选/多选题被判无选项）: {len(fn)}/{len(choice)}  -> "
          f"{', '.join(r['id'] for r in fn) or '无'}")
    print(f"  填空位命中                        : {len(fill_hit)}/{len(fills)}")
    print(f"  结构层命中                        : {sum(1 for r in rows if r['struct'])}/{len(rows)}")

    # ---- 章节 ----
    print()
    print("=" * 100)
    print("章节候选（规则层 suggest_chapter_candidates）")
    print("=" * 100)
    top1 = top3 = top8 = 0
    for r in rows:
        expected = str(r["case"].get("chapter_code", ""))
        codes = [c for c, _ in suggest_chapter_candidates(r["case"]["content"], 8)]
        h1 = bool(codes) and codes[0] == expected
        h3 = expected in codes[:3]
        h8 = expected in codes[:8]
        top1 += h1
        top3 += h3
        top8 += h8
        flag = "OK " if h1 else ("~3 " if h3 else ("~8 " if h8 else "MISS"))
        print(f"  {flag} {r['id']:<8} 期望={expected:<12} top3={', '.join(codes[:3]) or '（空）'}")
    n = len(rows)
    print(f"\n  top-1 {top1}/{n} = {top1/n:.1%}   top-3 {top3}/{n} = {top3/n:.1%}   "
          f"top-8 {top8}/{n} = {top8/n:.1%}")

    # ---- 难度 ----
    print()
    print("=" * 100)
    print("难度先验（规则层 estimate_difficulty_prior）")
    print("=" * 100)
    levels = {"easy": 0, "medium": 1, "hard": 2}
    exact = within = off2 = 0
    for r in rows:
        expected = str(r["case"].get("difficulty", ""))
        prior, signals = estimate_difficulty_prior(r["case"]["content"])
        delta = abs(levels.get(prior, 1) - levels.get(expected, 1))
        exact += delta == 0
        within += delta <= 1
        off2 += delta >= 2
        tag = "OK  " if delta == 0 else ("±1  " if delta == 1 else "差2档")
        print(f"  {tag} {r['id']:<8} 标注={expected:<7} 先验={prior:<7} 信号={signals}")
    print(f"\n  完全一致 {exact}/{n} = {exact/n:.1%}   ±1档 {within}/{n} = {within/n:.1%}   "
          f"差2档 {off2}/{n} = {off2/n:.1%}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
