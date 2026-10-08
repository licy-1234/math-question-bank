#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""final_cases.json 静态校验（构造完成后立即跑，不依赖评测代码）。

校验项
------
1. JSON 合法、字段齐全、id 唯一；
2. chapter_code 全部存在于课程节点树（160 节点）；
3. 四类题型各 ≥10、latex/plaintext 双形态覆盖；
4. 与 cases.json（66）+ holdout_cases.json（48）逐条 4-gram Jaccard，最高值必须 < 0.30；
5. 薄弱形态加码是否达标（无提示语多选、三类小问编号、天干人名、同形干扰、填空空位形态）。
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathbank.tags import curriculum_index  # noqa: E402

FINAL = TOOLS_DIR / "final_cases.json"
PRIOR = {
    "cases.json（原集66）": TOOLS_DIR / "cases.json",
    "holdout_cases.json（留出集48）": TOOLS_DIR / "holdout_cases.json",
}
SIM_LIMIT = 0.30
REQUIRED = ("id", "form", "difficulty", "chapter_code", "modality", "source_hint", "content")
FORMS = ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer")


def load(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = payload["cases"] if isinstance(payload, dict) else payload
    return [c for c in cases if isinstance(c, dict)]


def grams(text: str, k: int = 4) -> set[str]:
    return {text[i:i + k] for i in range(len(text) - k + 1)}


def main() -> int:
    final = load(FINAL)
    print("=" * 92)
    print(f"静态校验：{FINAL.name}   共 {len(final)} 条")
    print("=" * 92)

    ok = True

    # ---- 1. 字段完整性 ----
    print("\n[1] JSON / 字段完整性")
    missing = []
    for c in final:
        for key in REQUIRED:
            if key not in c:
                missing.append((c.get("id", "?"), key))
    ids = [c["id"] for c in final]
    dup = [i for i, n in Counter(ids).items() if n > 1]
    bad_diff = [c["id"] for c in final if c["difficulty"] not in ("easy", "medium", "hard")]
    bad_form = [c["id"] for c in final if c["form"] not in FORMS]
    bad_modal = [c["id"] for c in final if c["modality"] not in ("latex", "plaintext")]
    for label, val in (
        ("缺字段", missing), ("id 重复", dup),
        ("difficulty 非法", bad_diff), ("form 非法", bad_form), ("modality 非法", bad_modal),
    ):
        print(f"    {label:<18}: {'无' if not val else val}")
        ok = ok and not val

    # ---- 2. chapter_code 必须在 160 节点树里 ----
    print("\n[2] chapter_code 合法性（课程节点树）")
    index = curriculum_index("A")
    bad_code = [(c["id"], c["chapter_code"]) for c in final if c["chapter_code"] not in index]
    print(f"    课程节点总数: {len(index)}")
    print(f"    不在节点树中的 chapter_code: {'无' if not bad_code else bad_code}")
    ok = ok and not bad_code
    print(f"    覆盖到的节点数: {len({c['chapter_code'] for c in final})}")
    print(f"    按册分布: {dict(Counter(c['chapter_code'].split('-')[0] for c in final))}")

    # ---- 3. 分布 ----
    print("\n[3] 题型 / 形态分布")
    by_form = Counter(c["form"] for c in final)
    for form in FORMS:
        n = by_form.get(form, 0)
        sub = Counter(c["modality"] for c in final if c["form"] == form)
        flag = "OK" if n >= 10 and sub.get("latex", 0) > 0 and sub.get("plaintext", 0) > 0 else "FAIL"
        print(f"    {flag}  {form:<16} {n:>3} 条   latex={sub.get('latex',0)}  plaintext={sub.get('plaintext',0)}")
        ok = ok and n >= 10 and sub.get("latex", 0) > 0 and sub.get("plaintext", 0) > 0
    print(f"    难度分布: {dict(Counter(c['difficulty'] for c in final))}")

    # ---- 4. 与前两集的雷同度 ----
    print("\n[4] 与前两集的 4-gram Jaccard 雷同度（阈值 < 0.30）")
    worst_overall = 0.0
    worst_desc = ""
    for label, path in PRIOR.items():
        prior = load(path)
        prior_g = [(grams(c["content"]), c["id"]) for c in prior]
        rows = []
        for c in final:
            g = grams(c["content"])
            best = max(((len(g & pg) / len(g | pg)), pid) for pg, pid in prior_g)
            rows.append((best[0], c["id"], best[1]))
        rows.sort(reverse=True)
        over = [r for r in rows if r[0] >= SIM_LIMIT]
        print(f"    对照 {label}: 最高 {rows[0][0]:.3f}（{rows[0][1]} ↔ {rows[0][2]}），"
              f"超限 {len(over)} 条")
        for score, nid, pid in rows[:3]:
            print(f"        {score:.3f}  {nid} ↔ {pid}")
        for score, nid, pid in over:
            print(f"        ❌ 超限 {score:.3f}  {nid} ↔ {pid}")
        if rows and rows[0][0] > worst_overall:
            worst_overall = rows[0][0]
            worst_desc = f"{rows[0][1]} ↔ {label} 的 {rows[0][2]}"
        ok = ok and not over
    print(f"    → 全局最高雷同度 {worst_overall:.3f}（{worst_desc}）"
          f"  {'✅ 达标' if worst_overall < SIM_LIMIT else '❌ 未达标'}")

    # ---- 5. 薄弱形态加码 ----
    print("\n[5] 薄弱形态加码是否达标")
    multi_no_prompt = [
        c["id"] for c in final
        if c["form"] == "multi_choice"
        and not re.search(r"多选题|多项|多选|选出所有|全部选对|部分选对|选对但不全|不止一个|不止一项", c["content"])
    ]
    circled_sub = [
        c["id"] for c in final
        if c["form"] == "detailed_answer" and len(re.findall(r"[①②③④⑤⑥]", c["content"])) >= 3
    ]
    enum_item = [
        c["id"] for c in final
        if c["form"] == "detailed_answer"
        and re.search(r"\\begin\s*\{\s*enumerate", c["content"])
        and len(re.findall(r"\\item\b", c["content"])) >= 3
    ]
    paren_sub = [
        c["id"] for c in final
        if c["form"] == "detailed_answer" and len(re.findall(r"\(\s*[123]\s*\)", c["content"])) >= 2
    ]
    tiangan = [
        c["id"] for c in final
        if re.search(r"[甲][、，]\s*[乙]", c["content"])
    ]
    noise_map = {
        "角A、B、C的对边/所对的边": r"角A、B、C",
        "集合A、B": r"集合A",
        "椭圆C：": r"椭圆C：",
        "∁_U(A∪B)": r"∁",
        "按A:B=": r"按A:B",
        "交于A、B两点": r"交于A、B",
        "D、E分别为": r"D、E分别",
    }
    noise_hits = {}
    for label, pattern in noise_map.items():
        hits = [c["id"] for c in final if re.search(pattern, c["content"])]
        noise_hits[label] = hits
    blanks = {
        r"\fillin": [c["id"] for c in final if r"\fillin" in c["content"]],
        r"\underline{\quad}": [c["id"] for c in final if r"\underline{\quad}" in c["content"]],
        r"\underline{\qquad}": [c["id"] for c in final if r"\underline{\qquad}" in c["content"]],
        "__________": [c["id"] for c in final if "__________" in c["content"]],
        "（    ）": [c["id"] for c in final if re.search(r"（\s{4,}）", c["content"])],
        "［　　］": [c["id"] for c in final if "［　　］" in c["content"]],
        "多空": [c["id"] for c in final if len(re.findall(r"\\fillin|\\underline|__________|［　　］|（\s{4,}）", c["content"])) >= 2],
        "提示语": [c["id"] for c in final if "把答案填在" in c["content"]],
    }

    def report(label, hits, minimum):
        nonlocal ok
        good = len(hits) >= minimum
        ok = ok and good
        print(f"    {'OK ' if good else 'FAIL'} {label:<28} {len(hits):>2}/{minimum}  {hits if len(hits) <= 8 else hits[:8] + ['…']}")

    report("多选题·无任何提示语", multi_no_prompt, 6)
    report("解答题·①②③ 编号", circled_sub, 2)
    report("解答题·enumerate+item", enum_item, 2)
    report("解答题·(1)(2)(3) 编号", paren_sub, 2)
    report("天干人名/箱名干扰", tiangan, 3)
    report("同形干扰串（合计条目）", sorted({i for v in noise_hits.values() for i in v}), 4)
    for label, hits in noise_hits.items():
        print(f"         └ {label}: {hits if hits else '—'}")
    print("    填空空位形态：")
    for label, hits in blanks.items():
        print(f"         └ {label:<20} {hits if hits else '—'}")

    print("\n" + "=" * 92)
    print("静态校验结论：" + ("全部通过 ✅" if ok else "存在未通过项 ❌"))
    print("=" * 92)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
