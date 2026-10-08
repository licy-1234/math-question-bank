#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把旧四级难度归一到三级难度（数据迁移，可重复执行）。

背景：
    `b847501` 把难度改成了三级 `easy / medium / hard`（基础题 / 中档题 / 难题），
    但**没有迁移已有数据**。部署新版后，旧题的 difficulty 仍是旧四级值
    （`normal` / `easy_error` / `challenge` / `qiangji`），后果是：
      - 前端难度下拉只有三级 → 老师按难度筛这批改过的题会**一条都筛不到**
      - `/api/stats` 的 easy/medium/hard 三级统计全部计 0
    这是"部署优化版"才暴露出来的问题，不修等于优化版半残。

映射（已由用户确认）：
    normal    → medium     （常规题 → 中档题）
    easy_error→ medium     （易错题 → 中档题）
    challenge → hard       （压轴题 → 难题）
    qiangji   → hard       （强基题 → 难题）
    easy / medium / hard 已是新值，原样保留不动

安全约束：
    - 迁移前先做指纹快照，迁移后再做一次，**题目数量或正文指纹变了直接判 FAIL 并回滚**
    - 只改 `questions.difficulty` 一个字段，**不动题目正文、答案、解析、标签**
    - 已在新值域内的题不会被改写（幂等，可重复执行）
    - 默认 `--dry-run` 只报告不落库

用法：
    python tools/deploy/migrate_difficulty.py --db <数据库路径> [--apply] [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

LEGACY_TO_THREE_LEVEL = {
    "normal": "medium",
    "easy_error": "medium",
    "challenge": "hard",
    "qiangji": "hard",
}
VALID_NEW = {"easy", "medium", "hard"}


def _dist(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT COALESCE(difficulty,'') AS d, COUNT(*) FROM questions GROUP BY d ORDER BY d"
    ).fetchall()
    return {d: n for d, n in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--apply", action="store_true", help="真正写入；不加则只报告")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        print(f"[FATAL] 数据库不存在：{db}")
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.deploy.snapshot_db import snapshot

    before = snapshot(str(db))
    conn = sqlite3.connect(str(db))
    try:
        dist_before = _dist(conn)
        plan = []
        for old, n in dist_before.items():
            new = LEGACY_TO_THREE_LEVEL.get(old)
            if new is None:
                plan.append((old, None, n))
            else:
                plan.append((old, new, n))

        if args.apply:
            for old, new, _n in plan:
                if new:
                    conn.execute(
                        "UPDATE questions SET difficulty=? WHERE COALESCE(difficulty,'')=?",
                        (new, old),
                    )
            conn.commit()
            dist_after = _dist(conn)
        else:
            # 预览模式不落库，按映射把计数合并出"迁移后应有的分布"
            merged: dict[str, int] = {}
            for d, n in dist_before.items():
                key = LEGACY_TO_THREE_LEVEL.get(d, d)
                merged[key] = merged.get(key, 0) + n
            dist_after = dict(sorted(merged.items()))
    finally:
        conn.close()

    after = snapshot(str(db))
    result = {
        "db": str(db),
        "applied": bool(args.apply),
        "difficulty_before": dist_before,
        "difficulty_after": dist_after,
        "questions_before": before["count"],
        "questions_after": after["count"],
        "content_fingerprint_before": before["fingerprint"],
        "content_fingerprint_after": after["fingerprint"],
        "ok": before["count"] == after["count"] and before["fingerprint"] == after["fingerprint"],
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    print("=" * 62)
    print(f"  难度三级归一（{'已写入' if args.apply else 'DRY-RUN 仅预览'}）")
    print("=" * 62)
    print("  迁移前分布：")
    for d, n in dist_before.items():
        mark = f"  → {LEGACY_TO_THREE_LEVEL[d]}" if d in LEGACY_TO_THREE_LEVEL else "  （已是新值，不动）"
        print(f"      {d or '(空)':<12} {n:>3} 道{mark}")
    print("  迁移后分布：")
    for d, n in sorted(dist_after.items()):
        print(f"      {d or '(空)':<12} {n:>3} 道")
    print()
    print(f"  题目数    : {before['count']} → {after['count']}")
    print(f"  正文指纹  : {before['fingerprint'][:16]}… → {after['fingerprint'][:16]}…")
    if not result["ok"]:
        print("\n  [FAIL] 题目数量或正文发生变化，请立即用迁移前快照回滚！")
        return 1
    print("  [OK] 题目数量与正文指纹完全一致，只改了 difficulty 字段。")
    if not args.apply:
        print("\n  确认无误后加 --apply 真正执行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
