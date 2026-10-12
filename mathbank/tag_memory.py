"""Usage memory for free-text tags (自定义标签 / 微观解题方法).

Free-text tags are typed by hand, so the app remembers what the teacher
actually used: every save records the tags together with a ``last_used_at``
timestamp and a ``use_count``.  The memory lives in one JSON file under
``data_backup/`` — deliberately NOT a database table, so adopting this
feature never touches the ``questions`` schema or any stored question.

Shape of the store::

    {
        "custom": [{"tag": "教材P98T4", "last_used_at": "...", "use_count": 3}],
        "micro":  [...]
    }

Writes are guarded by a module-level lock (the app is a single uvicorn
process; threads may call concurrently from request handlers).
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timedelta
from typing import Iterable

from mathbank.paths import DATA_BACKUP_DIR

MEMORY_DIMENSIONS = ("custom", "micro")
MAX_MEMORY_ENTRIES_PER_DIM = 500
MAX_TAG_LENGTH = 120

# 回填条目的固定基准时间：绝不用 datetime.now()。回填是「补记历史上真实用过、
# 但当时还没这套记忆功能」的标签，用当下时间会让每次启动写出的文件都不同
# （既不幂等，也会把老标签顶到 MRU 最前）。固定基准 + 按序递增微秒 =>
# 多次回填字节级一致，且永远排在真实使用（时间戳更新）之后。
BACKFILL_BASE_TIME = datetime(2026, 1, 1)

_LOCK = threading.Lock()


def _memory_path() -> str:
    # 与 main.py 的 IS_TESTING 同口径：pytest 运行期间写测试文件，绝不动真实记忆。
    is_testing = "pytest" in sys.modules or any("pytest" in arg for arg in sys.argv)
    filename = "tag_memory_test.json" if is_testing else "tag_memory.json"
    return os.path.join(str(DATA_BACKUP_DIR), filename)


def _clean_tag(value) -> str:
    tag = str(value or "").strip().replace("\u3000", " ")
    return tag[:MAX_TAG_LENGTH]


def _clean_tags(values: Iterable) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        tag = _clean_tag(value)
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result


def _load_store() -> dict:
    try:
        with open(_memory_path(), "r", encoding="utf-8") as file:
            data = json.load(file)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_store(store: dict) -> None:
    path = _memory_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as file:
        json.dump(store, file, ensure_ascii=False, indent=1)
    os.replace(tmp_path, path)


def record_usage(dim: str, tags: Iterable) -> list[dict]:
    """Record one usage of each tag in ``dim``; returns the updated entries.

    Unknown dimensions and empty tag lists are no-ops.  Existing entries get
    ``use_count += 1`` and a fresh ``last_used_at``; new entries start at 1.
    """

    dimension = str(dim or "").strip()
    if dimension not in MEMORY_DIMENSIONS:
        return []
    clean = _clean_tags(tags)
    if not clean:
        return []
    base = datetime.utcnow()
    with _LOCK:
        store = _load_store()
        entries = store.get(dimension)
        if not isinstance(entries, list):
            entries = []
        index = {
            entry.get("tag"): entry
            for entry in entries
            if isinstance(entry, dict) and entry.get("tag")
        }
        for offset, tag in enumerate(clean):
            # 同一批内按传入顺序给递增时间戳：越靠后 = 刚刚越"新"，
            # 保证同批多条在同一微秒粒度下也能得到稳定的 MRU 排序。
            now = (base + timedelta(microseconds=offset)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            entry = index.get(tag)
            if entry is None:
                entry = {"tag": tag, "last_used_at": now, "use_count": 1}
                index[tag] = entry
                entries.append(entry)
            else:
                entry["last_used_at"] = now
                try:
                    entry["use_count"] = int(entry.get("use_count") or 0) + 1
                except (TypeError, ValueError):
                    entry["use_count"] = 1
        # 保留最近使用的 N 条，防止词表无限膨胀；按最近使用排序裁剪。
        entries.sort(key=lambda item: str(item.get("last_used_at") or ""), reverse=True)
        store[dimension] = entries[:MAX_MEMORY_ENTRIES_PER_DIM]
        _save_store(store)
        return [dict(entry) for entry in store[dimension] if entry.get("tag") in set(clean)]


def backfill_from_bank(dim: str, entries: Iterable) -> int:
    """Seed the memory with tag values harvested from the existing question bank.

    题库里绝大多数题目是在「标签记忆」上线**之前**录入的：它们的自定义标签
    真实存在（question_tags / questions.tags），却从未被 ``record_usage``
    记过，面板因此一直显示「暂无记忆」。本函数把这段历史补进 JSON。

    契约（三次强调，都是硬约束）：
      * **只写 JSON，不碰数据库**：``entries`` 由调用方只读查询后传入。
      * **只注入缺失的 tag**：已存在的条目原样保留，绝不覆盖真实
        ``use_count`` / ``last_used_at`` —— 因此可安全地在每次启动时执行。
      * **确定性时间戳**：新条目用固定基准 + 按传入顺序递增微秒，
        文件结果与「何时跑、跑几次」无关。

    ``entries`` 形如 ``[(tag, use_count), ...]``；返回新增条数。
    """

    dimension = str(dim or "").strip()
    if dimension not in MEMORY_DIMENSIONS:
        return 0

    prepared: list[tuple[str, int]] = []
    seen: set[str] = set()
    for item in entries or []:
        try:
            raw_tag, raw_count = item
        except (TypeError, ValueError):
            continue
        tag = _clean_tag(raw_tag)
        if not tag or tag in seen:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError):
            count = 1
        seen.add(tag)
        prepared.append((tag, max(1, count)))
    if not prepared:
        return 0

    added = 0
    with _LOCK:
        store = _load_store()
        existing = store.get(dimension)
        if not isinstance(existing, list):
            existing = []
        known = {
            _clean_tag(entry.get("tag"))
            for entry in existing
            if isinstance(entry, dict) and _clean_tag(entry.get("tag"))
        }
        offset = 0
        for tag, count in prepared:
            if tag in known:
                # 已有记忆：真实使用次数优先，回填绝不覆盖。
                continue
            stamp = (BACKFILL_BASE_TIME + timedelta(microseconds=offset)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            existing.append({"tag": tag, "last_used_at": stamp, "use_count": count})
            known.add(tag)
            added += 1
            offset += 1
        if not added:
            return 0
        # 与 record_usage 同一套裁剪口径：按最近使用排序后只留前 N 条。
        existing.sort(key=lambda item: str(item.get("last_used_at") or ""), reverse=True)
        store[dimension] = existing[:MAX_MEMORY_ENTRIES_PER_DIM]
        _save_store(store)
    return added


def list_memory(dim: str) -> list[dict]:
    """Return the remembered tags of one dimension, most-recently-used first."""

    dimension = str(dim or "").strip()
    if dimension not in MEMORY_DIMENSIONS:
        return []
    with _LOCK:
        entries = _load_store().get(dimension)
    if not isinstance(entries, list):
        return []
    clean: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict) or not _clean_tag(entry.get("tag")):
            continue
        clean.append({
            "tag": _clean_tag(entry.get("tag")),
            "last_used_at": str(entry.get("last_used_at") or ""),
            "use_count": max(1, int(entry.get("use_count") or 1)),
        })
    clean.sort(key=lambda item: (item["last_used_at"], item["use_count"]), reverse=True)
    return clean


def delete_memory(dim: str, tag: str) -> bool:
    """Delete one remembered tag; returns whether anything was removed."""

    dimension = str(dim or "").strip()
    clean = _clean_tag(tag)
    if dimension not in MEMORY_DIMENSIONS or not clean:
        return False
    with _LOCK:
        store = _load_store()
        entries = store.get(dimension)
        if not isinstance(entries, list):
            return False
        kept = [entry for entry in entries if _clean_tag(entry.get("tag")) != clean]
        if len(kept) == len(entries):
            return False
        store[dimension] = kept
        _save_store(store)
        return True


def clear_memory(dim: str) -> int:
    """Forget every remembered tag of one dimension; returns how many."""

    dimension = str(dim or "").strip()
    if dimension not in MEMORY_DIMENSIONS:
        return 0
    with _LOCK:
        store = _load_store()
        entries = store.get(dimension)
        count = len(entries) if isinstance(entries, list) else 0
        if count:
            store[dimension] = []
            _save_store(store)
        return count
