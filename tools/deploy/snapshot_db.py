#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""题库快照工具（只读）

用途：在任何写操作前后对题库做指纹快照，用于证明"题目主体内容未被删除/覆盖/改变"。

用法：
    python tools/deploy/snapshot_db.py <db_path> [--out 输出json路径]

指纹范围（重要，别误解）：
    - ``fingerprint`` 是**查询结果**的 SHA256（逐题 ``id|content``），不是数据库文件
      的字节哈希。SQLite 读取时会自动合并 -wal 中已提交但未 checkpoint 的内容，
      所以这个指纹天然覆盖 WAL，不存在"主文件没变但数据变了"的盲区。
    - 若将来改成对文件字节做哈希，就必须先 ``PRAGMA wal_checkpoint(TRUNCATE)``
      或把 ``-wal`` / ``-shm`` 一起纳入，否则会漏掉未落盘的变更。本工具因此
      额外输出 journal_mode 与 -wal/-shm 的大小，便于核对当前是否存在未落盘数据。

安全护栏（都是踩过坑才加的，不要删）：
    - 输入必须是**已存在**且带 SQLite 文件头的数据库。早先版本对不存在的路径
      会静默建一个 0 字节的 sqlite 文件，看起来像"数据库被清空了"。
    - 输出必须用显式的 `--out`。早先版本把第二个位置参数当输出路径，一次
      `snapshot_db.py 主库 备份库` 就把备份库覆盖成了 JSON —— 备份当场报废。
      现在额外拒绝写入已存在的文件、以及任何像数据库的文件名。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


SQLITE_HEADER = b"SQLite format 3\x00"
#: 这些名字一律不允许作为 --out 目标：覆盖任何一个都是数据事故。
DATABASE_LIKE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3")


def assert_existing_database(path: Path) -> None:
    """拒绝一切"看起来像数据库、其实不是"的输入。

    ``sqlite3.connect`` 会在路径不存在时**新建**一个 0 字节库，随后所有
    查询报 "no such table" 或语法错，极易被误读成"题库被清空了"。
    """
    if not path.exists():
        raise SystemExit(f"[拒绝] 数据库不存在（不会新建）：{path}")
    if not path.is_file():
        raise SystemExit(f"[拒绝] 不是文件：{path}")
    with path.open("rb") as handle:
        if handle.read(len(SQLITE_HEADER)) != SQLITE_HEADER:
            raise SystemExit(
                f"[拒绝] 不是 SQLite 数据库（文件头不符）：{path}\n"
                f"       若它是 JSON 快照或其它文本，说明已被误写，请从备份恢复。"
            )


def assert_safe_output(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"[拒绝] 输出文件已存在，拒绝覆盖：{path}")
    lowered = path.name.lower()
    if lowered.endswith(DATABASE_LIKE_SUFFIXES):
        raise SystemExit(
            f"[拒绝] 拒绝写入像数据库的文件名：{path}\n"
            f"       快照是 JSON，请用 --out <名字>.json。"
        )
    if path.suffix.lower() not in {".json", ".txt"}:
        raise SystemExit(f"[拒绝] 输出文件必须是 .json 或 .txt：{path}")


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r[1] for r in rows]


def snapshot(db_path: str) -> dict:
    path = Path(db_path)
    assert_existing_database(path)
    # 注意：WAL 模式下即使只读也需要 -shm 可写，因此这里不使用 mode=ro，
    # 仅执行 SELECT，逻辑上仍是只读操作（且已通过文件头校验，不会新建库）。
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
        # WAL 相关的可观测信息：只为审计"是否有未落盘数据"，不做任何写操作
        # （wal_checkpoint 会写库，绝不能在这里调用）。
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        sidecars = {}
        for suffix in ("-wal", "-shm"):
            side = Path(str(path) + suffix)
            sidecars[suffix] = side.stat().st_size if side.exists() else 0
        return {
            "db_path": str(path),
            "db_size": path.stat().st_size,
            "journal_mode": journal_mode,
            "wal_size": sidecars["-wal"],
            "shm_size": sidecars["-shm"],
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 显式 --out 而不是第二个位置参数：位置参数版本会把"第二个数据库"
    # 当成输出目标，一次误用就能把备份库写成 JSON。
    out = None
    if "--out" in argv:
        index = argv.index("--out")
        if index + 1 >= len(argv):
            print("[拒绝] --out 缺少路径")
            return 2
        out = argv[index + 1]
        del argv[index : index + 2]
    if len(argv) != 1:
        print(__doc__)
        return 2

    data = snapshot(argv[0])
    text = json.dumps(data, ensure_ascii=False, indent=2)
    if out:
        out_path = Path(out)
        assert_safe_output(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
        print(f"written: {out_path}")
    print(f"count={data['count']} fingerprint={data['fingerprint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
