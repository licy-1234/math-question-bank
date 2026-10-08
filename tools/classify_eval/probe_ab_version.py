# -*- coding: utf-8 -*-
"""A/B 版本对照：把 HEAD~1（返工前）与 HEAD（返工后）的 classify_rules 同时加载，
对同一批「模型判错」输入比较行为，量化本次返工引入的回归。

不修改仓库内任何文件；旧版本从 git 对象里取出后写到系统临时目录再动态加载。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402

from mathbank.classify_rules import classify_with_rules as new_fn  # noqa: E402


def load_old_module():
    src = subprocess.run(
        ["git", "show", "53ac9f1~1:mathbank/classify_rules.py"],
        cwd=str(ROOT), capture_output=True, check=True,
    ).stdout.decode("utf-8")
    tmp = Path(tempfile.gettempdir()) / "_old_classify_rules.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("_old_classify_rules", tmp)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_old_classify_rules"] = module
    spec.loader.exec_module(module)
    return module.classify_with_rules


def load(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return [c for c in (payload["cases"] if isinstance(payload, dict) else payload)
            if isinstance(c, dict)]


WRONG = {
    "single_choice": "multi_choice",
    "multi_choice": "single_choice",
    "fill_in_blank": "single_choice",
    "detailed_answer": "multi_choice",
}
LABEL = {"single_choice": "单选", "multi_choice": "多选",
         "fill_in_blank": "填空", "detailed_answer": "解答"}


def main() -> int:
    old_fn = load_old_module()
    sets = {
        "cases": ROOT / "tools/classify_eval/cases.json",
        "holdout": ROOT / "tools/classify_eval/holdout_cases.json",
        "final": ROOT / "tools/classify_eval/final_cases.json",
    }

    print("=" * 96)
    print("A/B 版本对照：返工前(53ac9f1~1) vs 返工后(53ac9f1) —— 同一批「模型判错」输入")
    print("=" * 96)
    print()

    agg = {"old_wrong": 0, "old_silent": 0, "new_wrong": 0, "new_silent": 0, "n": 0}
    for name, path in sets.items():
        cases = load(path)
        rows = []
        for case in cases:
            truth = str(case.get("form", ""))
            wrong_type = WRONG.get(truth)
            if wrong_type is None:
                continue
            payload = {
                "question_type": wrong_type,
                "difficulty": str(case.get("difficulty", "")),
                "chapter_code": str(case.get("chapter_code", "")),
            }
            content = str(case.get("content", ""))
            try:
                o = old_fn(content, dict(payload))
                old_pred, old_review = o["question_type"], bool(o.get("needs_review"))
            except Exception as exc:  # 返工前版本自身有解包 bug，单独计数
                old_pred, old_review = f"<异常:{type(exc).__name__}>", True
            n = new_fn(content, dict(payload))
            rows.append({
                "id": str(case.get("id", "")),
                "truth": truth,
                "old_pred": old_pred,
                "new_pred": n["question_type"],
                "old_review": old_review,
                "new_review": bool(n.get("needs_review")),
            })

        old_wrong = [r for r in rows if r["old_pred"] != r["truth"]]
        new_wrong = [r for r in rows if r["new_pred"] != r["truth"]]
        old_silent = [r for r in rows if r["old_pred"] != r["truth"] and not r["old_review"]]
        new_silent = [r for r in rows if r["new_pred"] != r["truth"] and not r["new_review"]]

        print(f"[{name}]  {len(rows)} 条")
        print(f"  返工前：跟错 {len(old_wrong):>3}   静默 {len(old_silent):>3}")
        print(f"  返工后：跟错 {len(new_wrong):>3}   静默 {len(new_silent):>3}")
        delta_w = len(new_wrong) - len(old_wrong)
        delta_s = len(new_silent) - len(old_silent)
        print(f"  变化  ：跟错 {delta_w:+d}   静默 {delta_s:+d}"
              f"   {'<<< 变差' if delta_s > 0 else ('改善' if delta_s < 0 else '持平')}")
        print()

        agg["old_wrong"] += len(old_wrong)
        agg["new_wrong"] += len(new_wrong)
        agg["old_silent"] += len(old_silent)
        agg["new_silent"] += len(new_silent)
        agg["n"] += len(rows)

    print("=" * 96)
    print("合计")
    print("=" * 96)
    n = agg["n"]
    print(f"  样本 {n} 条（每套题每条注入一个错误四值）")
    print(f"  返工前 跟着模型错 {agg['old_wrong']}/{n} = {agg['old_wrong'] / n * 100:.1f}%   "
          f"其中静默 {agg['old_silent']}/{n} = {agg['old_silent'] / n * 100:.1f}%")
    print(f"  返工后 跟着模型错 {agg['new_wrong']}/{n} = {agg['new_wrong'] / n * 100:.1f}%   "
          f"其中静默 {agg['new_silent']}/{n} = {agg['new_silent'] / n * 100:.1f}%")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
