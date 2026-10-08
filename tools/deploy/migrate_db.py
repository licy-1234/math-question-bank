#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对指定部署副本的题库执行 schema 迁移（v10 → v11，多值标签体系）。

背景：
    部署副本里的数据库长期停留在旧 schema（无 question_tags 表、无 db_migrations 表），
    因此即使代码更新到带标签体系的版本，标签也无处存储 —— 这是"看起来像回退"的第二层原因。

本脚本做的事：
    1. 迁移前用 snapshot_db 做指纹快照；
    2. 调用仓库自带的 mathbank.db_migrations.migrate_database()，
       它内部还会额外生成一份"迁移前一致性快照"（schema-v10-to-v11.<时间戳>.db + .sha256）；
    3. 迁移后再做一次指纹快照，并与迁移前逐项比对，证明题目数量与正文未变；
    4. 输出 question_tags 的回填情况。

用法（务必用部署副本自带的解释器，依赖才齐全）：
    <部署副本>/python/python.exe tools/deploy/migrate_db.py --project-root <部署副本目录>

    # 仅检查、不写入（推荐先跑一遍）
    ... --check-only
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path


def _boot(project_root: Path) -> None:
    """把部署副本目录放到 sys.path 最前，确保 import 的是副本里的代码与数据。"""
    root = str(project_root)
    sys.path.insert(0, root)
    import os

    os.chdir(root)


def _table_exists(db_path: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def _user_version(db_path: Path) -> int:
    conn = sqlite3.connect(str(db_path))
    try:
        return int(conn.execute("PRAGMA user_version").fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", required=True, help="部署副本根目录（含 mathbank/ 与 *.db）")
    ap.add_argument("--check-only", action="store_true", help="只报告当前 schema 状态，不执行迁移")
    args = ap.parse_args()

    root = Path(args.project_root).resolve()
    if not (root / "mathbank" / "db_migrations.py").exists():
        print(f"[FATAL] {root} 下找不到 mathbank/db_migrations.py")
        return 2
    _boot(root)

    from mathbank import db_migrations  # noqa: E402
    from mathbank.paths import DATABASE_FILE  # noqa: E402

    db_path = Path(DATABASE_FILE)
    print(f"部署副本 : {root}")
    print(f"数据库   : {db_path}")
    print(f"迁移前 schema version = {_user_version(db_path)}")
    print(f"question_tags 表存在  = {_table_exists(db_path, 'question_tags')}")
    print(f"程序支持的最新版本    = {db_migrations.LATEST_SCHEMA_VERSION}")

    if args.check_only:
        return 0

    from sqlalchemy import create_engine  # noqa: E402

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # 仓库根，用于 import tools
    from tools.deploy.snapshot_db import snapshot  # noqa: E402

    before = snapshot(str(db_path))
    print(f"\n[迁移前] 题目数={before['count']} 指纹={before['fingerprint']}")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    result = db_migrations.migrate_database(engine)
    print(f"\n[迁移结果] {json.dumps(result, ensure_ascii=False, default=str)}")

    after = snapshot(str(db_path))
    print(f"\n[迁移后] 题目数={after['count']} 指纹={after['fingerprint']}")
    print(f"迁移后 schema version = {_user_version(db_path)}")
    print(f"question_tags 表存在  = {_table_exists(db_path, 'question_tags')}")

    conn = sqlite3.connect(str(db_path))
    try:
        n = conn.execute("SELECT COUNT(*) FROM question_tags").fetchone()[0]
        dims = conn.execute(
            "SELECT dim, COUNT(*) FROM question_tags GROUP BY dim ORDER BY dim"
        ).fetchall()
        qn = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        tagged = conn.execute(
            "SELECT COUNT(DISTINCT question_id) FROM question_tags"
        ).fetchone()[0]
        print(f"\nquestion_tags 行数={n}，题目总数={qn}，已有标签的题目数={tagged}")
        print("各维度标签数：" + ", ".join(f"{d}={c}" for d, c in dims))
    finally:
        conn.close()

    if before["count"] != after["count"] or before["fingerprint"] != after["fingerprint"]:
        print("\n[FAIL] 题目数量或正文指纹发生变化！请立即用迁移前快照回滚。")
        return 1
    print("\n[OK] 题目数量与正文指纹完全一致，未删除/覆盖/改变任何题目主体内容。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
