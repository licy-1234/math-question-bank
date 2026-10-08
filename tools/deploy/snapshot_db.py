#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""题库快照工具（只读）

用途：在任何写操作前后对题库做指纹快照，用于证明"题目主体内容未被删除/覆盖/改变"。

用法：
    python tools/deploy/snapshot_db.py <db_path> [输出json路径]

输出：
    - 题目总数
    - 每道题的 id / 题型 / 难度 / 章节 / 正文 sha256
    - 全库指纹（所有题目正文拼接后的 sha256）
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def snapshot(db_path: str) -> dict:
    path = Path(db_path)
    # 注意：WAL 模式下即使只读也需要 -shm 可写，因此这里不使用 mode=ro，
    # 仅执行 SELECT，逻辑上仍是只读操作。
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        cols = table_columns(conn, "questions")
        pick = [c for c in ("id", "question_type", "difficulty", "content",
                            "answer", "analysis", "compulsory", "chapter",
                            "knowledge", "created_at") if c in cols]
        sql = f"SELECT {', '.join(pick)} FROM questions ORDER BY id"
        rows = [dict(r) for r in conn.execute(sql).fetchall()]

        blob = []
        for r in rows:
            blob.append(f"{r.get('id')}|{r.get('content') or ''}")
        return {
            "db_path": str(path),
            "db_size": path.stat().st_size,
            "columns": cols,
            "count": len(rows),
            "fingerprint": _sha256("\n".join(blob)),
            "questions": [
                {
                    "id": r.get("id"),
                    "question_type": r.get("question_type"),
                    "difficulty": r.get("difficulty"),
                    "compulsory": r.get("compulsory"),
                    "chapter": r.get("chapter"),
                    "knowledge": r.get("knowledge"),
                    "content_sha256": _sha256(r.get("content") or ""),
                    "content_len": len(r.get("content") or ""),
                    "content_head": (r.get("content") or "")[:60],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    db_path = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else None
    data = snapshot(db_path)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print(f"written: {out}")
    print(f"count={data['count']} fingerprint={data['fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
