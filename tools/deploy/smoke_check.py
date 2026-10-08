#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对**正在运行的**题库做一次冒烟验收（只读 + 自建自删一道临时题）。

⚠️ 本脚本会真实写入并删除一道临时题（标题含「【冒烟自建题】」），**不是纯只读**；
   如需零写入体检，请加 `--read-only`（跳过建题/改标签/删题相关检查项）。

用途：部署/重启之后跑一遍，确认"优化版本真的在跑、14 道题没少、标签不丢"。
它比单元测试更有说服力，因为打的是真实服务、真实数据库。

用法：
    python tools/deploy/smoke_check.py [--base http://127.0.0.1:8000] [--read-only]

退出码 0 = 全部通过；1 = 有失败项（含"临时题没删干净"，避免脏题静默留在题库里）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_DEFAULT = "http://127.0.0.1:8000"
TIMEOUT = 20

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  —— {detail}" if detail else ""))


def get(path: str, token: str | None = None):
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("X-Local-Token", token)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def post_form(path: str, data: dict, token: str, method: str = "POST"):
    body = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=body, method=method)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("X-Local-Token", token)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def delete(path: str, token: str):
    req = urllib.request.Request(BASE + path, method="DELETE")
    req.add_header("X-Local-Token", token)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=BASE_DEFAULT)
    ap.add_argument(
        "--read-only",
        action="store_true",
        help="只跑纯 GET 检查项，跳过建题/改标签/删题（第 9-16 项），对用户题库零写入",
    )
    args = ap.parse_args()
    global BASE
    BASE = args.base.rstrip("/")

    print("=" * 66)
    print(f"  运行态冒烟验收  {BASE}")
    print("=" * 66)

    # 令牌：页面注入的 window.__localToken
    html = urllib.request.urlopen(BASE + "/", timeout=TIMEOUT).read().decode("utf-8", "replace")
    m = re.search(r'__localToken\s*=\s*["\']([^"\']+)["\']', html)
    token = m.group(1) if m else ""
    check("能取到本地令牌", bool(token))

    stats = get("/api/stats")
    check("题目总数 == 14", stats.get("total_count") == 14, f"实际 {stats.get('total_count')}")
    check(
        "难度统计为三级口径",
        {"easy_count", "medium_count", "hard_count"}.issubset(stats)
        and not ({"normal_count", "easy_error_count", "challenge_count", "qiangji_count"} & set(stats)),
        f"easy={stats.get('easy_count')} medium={stats.get('medium_count')} hard={stats.get('hard_count')}",
    )
    check(
        "三级难度统计不全为 0",
        (stats.get("easy_count", 0) + stats.get("medium_count", 0) + stats.get("hard_count", 0)) == stats.get("total_count"),
    )

    schema = get("/api/config/tag-schema")
    # storage 形如 "question_tags(dim='chapter')" / "questions.difficulty"，
    # 这里只关心落在 question_tags 窄表里的那四个多值维度。
    dims = set()
    for d in schema.get("dimensions", []):
        m = re.search(r"dim='([^']+)'", str(d.get("storage") or ""))
        if m:
            dims.add(m.group(1))
    check("标签体系为 rja2019-tags-v1", schema.get("schema_version") == "rja2019-tags-v1")
    check("四个多值维度齐备", {"chapter", "thought", "function", "custom"}.issubset(dims), str(sorted(dims)))

    n_easy = len(get("/api/questions?difficulty=easy&page_size=100"))
    n_medium = len(get("/api/questions?difficulty=medium&page_size=100"))
    n_hard = len(get("/api/questions?difficulty=hard&page_size=100"))
    check(
        "按 difficulty=easy/medium/hard 三档筛选合计 == 总数",
        (n_easy + n_medium + n_hard) == 14,
        f"easy={n_easy} medium={n_medium} hard={n_hard}",
    )

    first = get("/api/questions/1")
    check("题目详情带 tag_codes", "tag_codes" in first, json.dumps(first.get("tag_codes"), ensure_ascii=False))

    # ---- 关键：部署后的多值标签"编辑不丢"行为 ----
    # 写操作前先记下题目总数基线：清理后必须回到这个数，写死 14 会让题库
    # 将来变多时误报，也会掩盖"临时题没删干净"。
    baseline_total = stats.get("total_count")
    cleanup_error = ""
    qid = None
    if args.read_only:
        print("\n  [--read-only] 跳过第 9-16 项（建题/改标签/删题），本次对用户题库零写入。")
    else:
        # 无论中途哪一项断言抛异常，都要尽量把临时题删掉，绝不让它留在用户题库里
        try:
            created = post_form(
                "/api/questions",
                {
                    "content": "【冒烟自建题】验证多值标签编辑不丢失，验收完即删",
                    "question_type": "detailed_answer",
                    "difficulty": "medium",
                    "category_compulsory": "选修一",
                    "category_chapter": "2. 直线和圆的方程",
                    "category_knowledge": "2.1 直线的倾斜角与斜率",
                    "tag_chapter_codes": json.dumps(["X1-C2-S1", "X1-C2-S4", "B1-C1-S2"], ensure_ascii=False),
                    "tag_thought_codes": json.dumps(["T01", "T02"], ensure_ascii=False),
                    "tag_function_code": "error_prone",
                    "tag_custom_tags": "月考,压轴改编",
                    "image_paths": "[]",
                },
                token,
            )
            qid = (created.get("question") or {}).get("id") or created.get("id")
            check("自建临时题成功", bool(qid), f"id={qid}")
            if not qid:
                return 1

            before = get(f"/api/questions/{qid}")["tag_codes"]
            check("写入 3 个章节码", before["chapter"] == ["X1-C2-S1", "X1-C2-S4", "B1-C1-S2"], str(before["chapter"]))

            # 不带 tag_chapter_codes、category_* 原样提交 —— 修复前这里会把 chapter 压成 1 个
            post_form(
                f"/api/questions/{qid}",
                {
                    "content": "【冒烟自建题】正文已改，章节字段未动",
                    "question_type": "detailed_answer",
                    "difficulty": "hard",
                    "category_compulsory": "选修一",
                    "category_chapter": "2. 直线和圆的方程",
                    "category_knowledge": "2.1 直线的倾斜角与斜率",
                    "image_paths": "[]",
                },
                token,
                method="PUT",
            )
            after = get(f"/api/questions/{qid}")["tag_codes"]
            check(
                "不带 tag_chapter_codes 的保存后章节仍是 3 个",
                after["chapter"] == ["X1-C2-S1", "X1-C2-S4", "B1-C1-S2"],
                f"修复前这里会变成 1 个；实际 {after['chapter']}",
            )
            check("思想方法未丢", after["thought"] == ["T01", "T02"], str(after["thought"]))
            check("功能标签未丢", after["function"] == ["error_prone"], str(after["function"]))
            check("自定义标签未丢", set(after["custom"]) == {"月考", "压轴改编"}, str(after["custom"]))
            check("难度确实改成 hard", get(f"/api/questions/{qid}")["difficulty"] == "hard")

            # 顺序：读回顺序应等于录入顺序
            check("章节顺序 = 录入顺序", after["chapter"] == ["X1-C2-S1", "X1-C2-S4", "B1-C1-S2"])
        finally:
            if qid:
                try:
                    delete(f"/api/questions/{qid}", token)
                    print(f"  已删除临时题 id={qid}")
                except Exception as exc:  # noqa: BLE001
                    cleanup_error = f"临时题 id={qid} 删除失败：{exc}"
                    check("清理临时题", False, cleanup_error)

    if not args.read_only:
        # 硬断言：清理后必须回到写操作前的题目总数基线，否则就是脏题残留。
        after_total = get("/api/stats").get("total_count")
        detail = f"清理前基线 {baseline_total}，清理后 {after_total}"
        if cleanup_error:
            detail = f"{cleanup_error}；{detail}"
        ok = after_total == baseline_total
        check(f"清理后题目数回到基线 {baseline_total}", ok, detail)
        if cleanup_error:
            print(
                f"\n  [严重] 题库里**可能残留了临时题 id={qid}**（标题含「【冒烟自建题】」）。\n"
                f"         请立即到页面手工删除它，或用部署前的备份恢复题库。\n"
                f"         本次冒烟判定为失败（退出码非 0）。"
            )

    print()
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print(f"  结论：{len(failed)} 项未通过 —— {', '.join(failed)}")
        return 1
    print(f"  结论：全部 {len(results)} 项通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
