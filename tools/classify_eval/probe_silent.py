# -*- coding: utf-8 -*-
"""探针：模型判错 + 规则层证据弱 —— 会不会静默接受错误结论？

设计要点
--------
1. 为了让 needs_review 只反映「题型这一路」，探针里的 ai_payload 一律给出
   **合法且正确** 的 difficulty / chapter_code（即模拟「模型其余字段都对，
   唯独题型判错」的最理想生产环境）。这样 needs_review 若为 True，一定是
   题型/章节检索自己触发的，而不是难度或章节缺失顺带触发的。
2. 对每个用例注入一种「错误的显式四值」，覆盖 4 类错误方向。
3. 判定「静默接受错误结论」= 最终 question_type 与真值不同 且 needs_review=False。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mathbank.classify_rules import classify_with_rules  # noqa: E402
from mathbank.tags import curriculum_index  # noqa: E402

SETS = {
    "cases": ROOT / "tools" / "classify_eval" / "cases.json",
    "holdout": ROOT / "tools" / "classify_eval" / "holdout_cases.json",
    "final": ROOT / "tools" / "classify_eval" / "final_cases.json",
}

# 真值 -> 注入的错误值（只注入「合法四值」，即模型自信但判错的情形）
WRONG = {
    "single_choice": "multi_choice",
    "multi_choice": "single_choice",
    "fill_in_blank": "single_choice",
    "detailed_answer": "multi_choice",
}

LABEL = {
    "single_choice": "单选",
    "multi_choice": "多选",
    "fill_in_blank": "填空",
    "detailed_answer": "解答",
}


def load(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return [c for c in (payload["cases"] if isinstance(payload, dict) else payload)
            if isinstance(c, dict)]


def run_one(case: dict, wrong_type: str):
    """给一个合法且正确的 difficulty/chapter，只把题型改错。"""
    code = str(case.get("chapter_code", ""))
    if code and code not in curriculum_index("A"):
        code = ""  # 标注不合法时留空，避免顺带触发章节复核
    payload = {
        "question_type": wrong_type,
        "difficulty": str(case.get("difficulty", "")),
        "chapter_code": code,
    }
    return classify_with_rules(str(case.get("content", "")), payload)


def probe(cases: list[dict], name: str) -> dict:
    index = curriculum_index("A")
    rows = []
    for case in cases:
        truth = str(case.get("form", ""))
        wrong_type = WRONG.get(truth)
        if wrong_type is None:
            continue
        fused = run_one(case, wrong_type)
        predicted = fused["question_type"]
        needs_review = bool(fused["needs_review"])
        wrong = predicted != truth
        silent = wrong and not needs_review
        rows.append({
            "id": str(case.get("id", "")),
            "truth": truth,
            "injected": wrong_type,
            "predicted": predicted,
            "source": fused["question_type_source"],
            "has_options": fused["evidence"]["options"]["has_options"],
            "blanks": fused["evidence"]["blanks"],
            "multi_signals": fused["evidence"]["multi_signals"],
            "needs_review": needs_review,
            "review_reasons": fused["review_reasons"],
            "is_fallback": fused["is_fallback"],
            "chapter_ok": str(case.get("chapter_code", "")) in index,
            "wrong": wrong,
            "silent": silent,
        })
    return {"set": name, "rows": rows}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", action="store_true", help="打印每条明细")
    args = parser.parse_args()

    print("=" * 92)
    print("探针：模型判错 + 合法完整 payload —— 错误结论会不会静默呈现？")
    print("=" * 92)
    print("注入方式：difficulty / chapter_code 均给正确合法值，只把 question_type 改成错误四值。")
    print("静默接受 = 最终题型 != 真值 且 needs_review == False")
    print()

    total_rows = []
    for name, path in SETS.items():
        if not path.exists():
            print(f"  跳过 {name}（文件不存在）")
            continue
        result = probe(load(path), name)
        rows = result["rows"]
        total_rows.extend(rows)

        print("-" * 92)
        print(f"[{name}]  {len(rows)} 条")
        wrong = [r for r in rows if r["wrong"]]
        silent = [r for r in rows if r["silent"]]
        caught = [r for r in rows if not r["wrong"]]
        flagged = [r for r in rows if r["wrong"] and r["needs_review"]]

        print(f"  规则层纠正回真值        : {len(caught)}/{len(rows)}")
        print(f"  跟着模型一起错          : {len(wrong)}/{len(rows)}")
        print(f"    ├─ 错且已提示复核      : {len(flagged)}")
        print(f"    └─ 错且静默（needs_review=False）: {len(silent)}   <<< 生产风险")
        print()

        # 按真值分组
        for truth in ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer"):
            sub = [r for r in rows if r["truth"] == truth]
            if not sub:
                continue
            sw = [r for r in sub if r["wrong"]]
            ss = [r for r in sub if r["silent"]]
            print(f"    真值 {LABEL[truth]:<3} 注入 {LABEL[WRONG[truth]]:<3}: "
                  f"错 {len(sw)}/{len(sub)}   静默 {len(ss)}/{len(sub)}")
        print()

        if args.detail:
            for r in rows:
                if r["wrong"]:
                    tag = "静默!!" if r["silent"] else "已提示"
                    print(f"    {tag} {r['id']:<8} 真={LABEL[r['truth']]} "
                          f"注入={LABEL[r['injected']]} 输出={LABEL.get(r['predicted'], r['predicted'])} "
                          f"src={r['source']:<10} 选项={r['has_options']} 空位={r['blanks']} "
                          f"多选提示={r['multi_signals']}")
            print()

    print("=" * 92)
    print("三集合合计")
    print("=" * 92)
    n = len(total_rows)
    wrong = [r for r in total_rows if r["wrong"]]
    silent = [r for r in total_rows if r["silent"]]
    print(f"  用例总数                 : {n}")
    print(f"  规则层成功纠正           : {n - len(wrong)}/{n} = {(n - len(wrong)) / n * 100:.1f}%")
    print(f"  跟着模型一起错           : {len(wrong)}/{n} = {len(wrong) / n * 100:.1f}%")
    print(f"  静默接受错误（未提示复核）: {len(silent)}/{n} = {len(silent) / n * 100:.1f}%")
    print()

    # 静默案例按错误方向归类
    by_dir: dict[str, int] = {}
    for r in silent:
        key = f"真值{LABEL[r['truth']]} -> 输出{LABEL.get(r['predicted'], r['predicted'])}"
        by_dir[key] = by_dir.get(key, 0) + 1
    if by_dir:
        print("  静默案例的错误方向分布：")
        for key, cnt in sorted(by_dir.items(), key=lambda kv: -kv[1]):
            print(f"    {key}: {cnt} 条")
    print()

    if silent:
        print("  静默案例清单（前 25 条）：")
        for r in silent[:25]:
            print(f"    {r['id']:<8} 真={LABEL[r['truth']]:<3} 注入={LABEL[r['injected']]:<3} "
                  f"输出={LABEL.get(r['predicted'], r['predicted']):<3} src={r['source']:<10} "
                  f"选项={str(r['has_options']):<5} 空位={str(r['blanks']):<5} 多选提示={r['multi_signals']}")
        print()

    print("  结论提示：静默案例的共同特征应为 "
          "「analyze_options 有选项」且「模型给了显式 single/multi」，")
    print("  即 decide_question_type 第 512-516 行的无条件采信分支。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
