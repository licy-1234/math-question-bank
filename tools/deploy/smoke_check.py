#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对**正在运行的**题库做一次冒烟验收（只读 + 自建自删一道临时题）。

用途：部署/重启之后跑一遍，确认"优化版本真的在跑、14 道题没少、标签不丢"。
它比单元测试更有说服力，因为打的是真实服务、真实数据库。

用法：
    python tools/deploy/smoke_check.py [--base http://127.0.0.1:8000]

退出码 0 = 全部通过；1 = 有失败项。
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

    n_medium = len(get("/api/questions?difficulty=medium&page_size=100"))
    n_hard = len(get("/api/questions?difficulty=hard&page_size=100"))
    check("按 difficulty=medium/hard 能筛到题", (n_medium + n_hard) == 14, f"medium={n_medium} hard={n_hard}")

    first = get("/api/questions/1")
    check("题目详情带 tag_codes", "tag_codes" in first, json.dumps(first.get("tag_codes"), ensure_ascii=False))

    # ---- 关键：部署后的多值标签"编辑不丢"行为 ----
    qid = None
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
            except Exception as exc:  # noqa: BLE001
                print(f"  [WARN] 临时题 {qid} 删除失败，请手工清理：{exc}")

    after_stats = get("/api/stats")
    check("清理后题目数回到 14", after_stats.get("total_count") == 14, f"实际 {after_stats.get('total_count')}")

    print()
    failed = [n for n, ok, _ in results if not ok]
    if failed:
        print(f"  结论：{len(failed)} 项未通过 —— {', '.join(failed)}")
        return 1
    print(f"  结论：全部 {len(results)} 项通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
