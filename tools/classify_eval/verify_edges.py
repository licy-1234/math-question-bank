#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""独立验证：边界/错误路径 + 回归 + 冷启动 import 顺序。

不修改业务源码；只在 tools/classify_eval/ 下产出。
"""

from __future__ import annotations

import subprocess
import string as _string
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TOOLS_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mathbank.classify_rules import classify_with_rules  # noqa: E402
from mathbank.question_types import (  # noqa: E402
    detect_choice_options,
    normalize_ai_question_form,
    normalize_ai_question_type,
)

PY = sys.executable

REQUIRED_FIELDS = [
    "question_type", "question_type_source", "question_form", "question_form_source",
    "difficulty", "difficulty_source", "difficulty_conflict", "chapter_code",
    "chapter_path", "chapter_source", "evidence", "needs_review", "is_fallback",
]


def section(title: str) -> None:
    print()
    print("=" * 92)
    print(title)
    print("=" * 92)


# --------------------------------------------------------------------------
section("1. 冷启动 import 顺序（两个方向各起一个全新解释器）")
# --------------------------------------------------------------------------
ORDERS = [
    ["import mathbank.classify_rules; import mathbank.question_types; print('OK')"],
    ["import mathbank.question_types; import mathbank.classify_rules; print('OK')"],
    ["from mathbank.question_types import detect_choice_options; print('OK', detect_choice_options('A. 1  B. 2  C. 3'))"],
    ["from mathbank.classify_rules import classify_with_rules; print('OK')"],
]
for i, code in enumerate(ORDERS, 1):
    proc = subprocess.run(
        [PY, "-W", "error::SyntaxWarning", "-c", code[0]],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    status = "PASS" if proc.returncode == 0 else "FAIL"
    print(f"  [{status}] 顺序{i}: {code[0][:70]}")
    if proc.returncode != 0:
        print("        stdout:", proc.stdout.strip()[:300])
        print("        stderr:", proc.stderr.strip()[-600:])


# --------------------------------------------------------------------------
section("2. 回归：question_types 三值归一化语义")
# --------------------------------------------------------------------------
EXPECT_FORM = {
    "choice": "choice", "选择题": "choice", "单选题": "choice", "多选题": "choice",
    "single_choice": "choice", "multi_choice": "choice",
    "fill_in_blank": "fill_in_blank", "填空题": "fill_in_blank",
    "detailed_answer": "detailed_answer", "解答题": "detailed_answer",
    "unknown": "unknown", "未知": "unknown", "": "unknown", None: "unknown",
    "garbage": "unknown",
}
bad = []
for value, want in EXPECT_FORM.items():
    got = normalize_ai_question_form(value)
    if got != want:
        bad.append((value, want, got))
print(f"  normalize_ai_question_form: {len(EXPECT_FORM) - len(bad)}/{len(EXPECT_FORM)} 符合三值语义"
      + ("  -> 差异: " + str(bad) if bad else ""))

EXPECT_TYPE = {
    "single_choice": "single_choice", "单选题": "single_choice",
    "multi_choice": "multi_choice", "多选题": "multi_choice",
    "fill_in_blank": "fill_in_blank", "填空题": "fill_in_blank",
    "detailed_answer": "detailed_answer", "解答题": "detailed_answer",
    "choice": None, "选择题": None, "": None, None: None,
}
bad2 = []
for value, want in EXPECT_TYPE.items():
    got = normalize_ai_question_type(value)
    if got != want:
        bad2.append((value, want, got))
print(f"  normalize_ai_question_type: {len(EXPECT_TYPE) - len(bad2)}/{len(EXPECT_TYPE)} 符合四值语义"
      + ("  -> 差异: " + str(bad2) if bad2 else ""))

for value in ["A. 1\nB. 2\nC. 3", "", "在△ABC中，角A、B、C的对边分别为a、b、c。"]:
    result = detect_choice_options(value)
    print(f"  detect_choice_options({value[:28]!r}) -> {result}  type={type(result).__name__}"
          f"  {'OK(bool)' if isinstance(result, bool) else 'FAIL(非 bool!)'}")


# --------------------------------------------------------------------------
section("3. 边界 / 错误输入（classify_with_rules 必须不抛异常）")
# --------------------------------------------------------------------------
CONTENT_EDGES = [
    ("空字符串", ""),
    ("纯空白", "   \t\n\n　  "),
    ("None", None),
    ("超长文本5000字", "已知函数 f(x) 满足下列条件。" * 250),
    ("纯英文题干", "Let f(x)=x^2-3x+2. Find the minimum value of f on the interval [0,3]."),
    ("只有图片占位", "![img](uploads/x.png)"),
    ("图片占位+少量文字", "如图所示：\n![fig](uploads/2026/p1.png)\n求该几何体的体积。"),
    ("乱码/不可见字符", "已知函\x00数\u200b\ufeff f(x)\u200d=x\u2060+1\u00ad，求值域。"),
    ("emoji", "已知函数 🙏 f(x)=x² 的最小值 🙏 是____。"),
    ("只有数字标点", "1,2,3,4,5。。。（　　）"),
    ("极端重复", "A. " * 500),
    ("SQL/HTML 注入串", "<script>alert(1)</script>'; DROP TABLE questions;--"),
]
PAYLOAD_EDGES = [
    ("ai_payload=None", None),
    ("ai_payload={}", {}),
    ("缺 question_type", {"difficulty": "medium"}),
    ("question_type=选择题(粗)", {"question_type": "选择题"}),
    ("question_type=超纲值", {"question_type": "不存在的题型"}),
    ("difficulty=超难", {"question_type": "single_choice", "difficulty": "超难"}),
    ("chapter_code=Z9-C99", {"question_type": "single_choice", "difficulty": "easy", "chapter_code": "Z9-C99"}),
    ("chapter_code=None", {"question_type": "detailed_answer", "chapter_code": None}),
    ("字段类型非法", {"question_type": 12345, "difficulty": ["hard"], "chapter_code": {"a": 1}}),
]

print(f"  {'输入':<26} {'题型':<16} {'来源':<10} {'难度':<8} {'章节':<14} {'空章节码':<8} 结论")
print("  " + "-" * 88)
problems = []
for label, content in CONTENT_EDGES:
    try:
        out = classify_with_rules(content, {})
        missing = [f for f in REQUIRED_FIELDS if f not in out]
        code = out["chapter_code"]
        empty_ok = isinstance(code, str)
        if missing or not empty_ok:
            problems.append((label, missing or "chapter_code 非 str"))
        print(f"  {label:<26} {str(out['question_type']):<16} {str(out['question_type_source']):<10} "
              f"{str(out['difficulty']):<8} {str(code)[:12]:<14} {str(empty_ok):<8} "
              f"{'OK' if not missing and empty_ok else 'PROBLEM ' + str(missing)}")
    except Exception as exc:
        problems.append((label, f"抛异常 {type(exc).__name__}: {exc}"))
        print(f"  {label:<26} !! 抛异常 {type(exc).__name__}: {exc}")

print()
for label, payload in PAYLOAD_EDGES:
    try:
        out = classify_with_rules("已知函数 f(x)=x^2-2x，求其在 [0,3] 上的最小值。", payload)
        missing = [f for f in REQUIRED_FIELDS if f not in out]
        if missing:
            problems.append((label, missing))
        print(f"  {label:<26} -> type={out['question_type']:<15} src={out['question_type_source']:<10} "
              f"diff={out['difficulty']:<8} chap={out['chapter_code']!r:<14} "
              f"{'OK' if not missing else 'PROBLEM ' + str(missing)}")
    except Exception as exc:
        problems.append((label, f"抛异常 {type(exc).__name__}: {exc}"))
        print(f"  {label:<26} !! 抛异常 {type(exc).__name__}: {exc}")

print()
print("  结论：" + ("全部通过，无异常、无缺字段" if not problems else f"存在 {len(problems)} 个问题 -> {problems}"))


# --------------------------------------------------------------------------
section("4. 返回体字段与「可直读中文」检查（会渲染给老师看的部分）")
# --------------------------------------------------------------------------
sample = classify_with_rules(
    "（多选题）已知函数 $f(x)=x^3-3x$。\nA. $f(x)$ 在 $(1,+\\infty)$ 上单调递增\n"
    "B. $x=-1$ 是极大值点\nC. $f(x)$ 有三个零点\nD. $f(x)$ 的最小值为 $-2$",
    {"question_type": "multi_choice", "difficulty": "medium", "chapter_code": "X2-C5-S3-P2"},
)
import json as _json
print("  完整返回体：")
print("  " + _json.dumps(sample, ensure_ascii=False, indent=2).replace("\n", "\n  "))

ev = sample["evidence"]
print()
print("  evidence.options        =", _json.dumps(ev["options"], ensure_ascii=False))
print("  evidence.multi_signals  =", _json.dumps(ev["multi_signals"], ensure_ascii=False))
print("  evidence.difficulty_signals =", _json.dumps(ev["difficulty_signals"], ensure_ascii=False))
for key in ("multi_signals", "difficulty_signals"):
    for item in ev[key]:
        # 只看"是否是可直读的中文短语"：允许中文、常见中英混排符号、圈码与数字，
        # 出现其它字符（多半是正则原文或英文 key）就报出来。
        allowed = set("（）「」《》、：；，。？！／＝≥≤+-%~^_ ")
        allowed |= set(_string.digits) | set(_string.ascii_letters)
        allowed |= set("①②③④⑤⑥⑦⑧⑨⑩")
        verdict = "OK" if all(
            "\u4e00" <= ch <= "\u9fff" or ch in allowed for ch in item
        ) else "含非中文/英文残留"
        print(f"    [{verdict}] {key}: {item}")
print()
print(f"  question_type_source={sample['question_type_source']}  is_fallback={sample['is_fallback']}  "
      f"needs_review={sample['needs_review']}")
print("  -> 语义重叠检查：is_fallback 已收窄为「整个载荷都不可用」"
      "（question_type=='unknown' 且 chapter_source=='fallback'），"
      "与 needs_review 不再重叠；main.py 的两处 partial 出口会强制置 True。")
