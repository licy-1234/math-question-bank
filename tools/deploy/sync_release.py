#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 Git 仓库中的源码同步到"实际运行的 Windows 部署副本"。

背景（题库"版本回退"根因）：
    真正被使用的程序是桌面上的一份解压副本（例如 C:\\Users\\...\\Desktop\\MathBank-Windows-x64），
    它 **不是 Git 仓库**。仓库里的新提交不会自动进入这份副本，于是页面看起来像"回退"了。
    本脚本把仓库 HEAD 的运行时文件同步过去，从而消除"仓库已更新、副本未更新"的割裂。

安全约束：
    - 只同步运行时需要的源码目录/文件；
    - **绝不触碰**用户数据：*.db / *.db-wal / *.db-shm / data_backup/；
    - **绝不覆盖**本地配置：.env（含 API Key）、RELEASE-MANIFEST.json、*.zip、python/（内置解释器）；
    - 被覆盖的旧文件先备份到 <dest>/_predeploy_backup_<时间戳>/ 再写入；
    - 默认只做"新增 + 覆盖"，**从不删除**目标端任何文件。

已修复的历史缺口：
    - `--prune` 曾经只是占位参数、传了等于没传（静默 no-op）。现在传入会**直接报错退出**并
      提示手工处理，避免使用者误以为已经清理过目标端。
    - `--since` 增量模式曾经只按 `_is_excluded()` 过滤、不走同步白名单，会把 `AGENTS.md` /
      `docs/**` / `.gitignore` 这类开发期文件带进用户副本。现在增量与全量**共用同一条白名单**
      （`_in_sync_scope()`）。

用法：
    python tools/deploy/sync_release.py --repo <仓库目录> --dest <部署副本目录> [--dry-run] [--since <提交>]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

# 需要同步的顶层路径（相对仓库根）
SYNC_TOP_LEVEL = [
    "main.py",
    "mathbank",
    "static",
    "templates",
    "scripts",
]

SYNC_SINGLE_FILES = [
    "requirements.txt",
    "requirements-windows.txt",
    "requirements-dev.txt",
    "README.md",
    "README_EN.md",
    ".env.example",
    "启动题库系统.bat",
]

# 硬性排除（永远不同步、不删除）
# tests / tools 属于开发期资产，不应进入用户侧的运行时副本
EXCLUDE_DIR_PARTS = {"__pycache__", ".git", ".system_generated", ".pytest_cache", "node_modules"}
EXCLUDE_TOP_LEVEL = {"tests", "tools", ".github", ".workbuddy"}
EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".db", ".db-wal", ".db-shm", ".zip", ".log"}

# 用户本地资产（不同步、不覆盖、不删除）
PRESERVE_NAMES = {".env", "RELEASE-MANIFEST.json", "覆盖升级说明.txt"}
PRESERVE_DIRS = {"python", "data_backup", "_predeploy_backup"}


def _is_preserved(rel: Path) -> bool:
    if rel.name in PRESERVE_NAMES:
        return True
    for part in rel.parts:
        if part in PRESERVE_DIRS or part.startswith("_predeploy_backup"):
            return True
    return False


def _is_excluded(rel: Path) -> bool:
    for part in rel.parts:
        if part in EXCLUDE_DIR_PARTS:
            return True
    if rel.parts and rel.parts[0] in EXCLUDE_TOP_LEVEL:
        return True
    if rel.suffix in EXCLUDE_SUFFIXES:
        return True
    return _is_preserved(rel)


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout


def list_repo_files(repo: Path) -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    files = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    # git 对中文路径会加引号，需还原
    import codecs
    decoded = []
    for f in files:
        if f.startswith('"') and f.endswith('"'):
            try:
                f = codecs.decode(f[1:-1].encode("utf-8"), "unicode_escape").encode("latin-1").decode("utf-8")
            except Exception:
                f = f[1:-1]
        decoded.append(f)
    return decoded


def _norm(b: bytes) -> bytes:
    """忽略 CRLF/LF 差异，避免把'仅换行符不同'误报为内容变更。"""
    return b.replace(b"\r\n", b"\n")


def _in_sync_scope(rel: Path) -> bool:
    """是否属于应进入用户运行时副本的范围。

    全量模式与 --since 增量模式必须共用这一条白名单，
    否则增量部署会把 AGENTS.md / docs/** 这类开发期文件带进用户副本。
    """
    top = rel.parts[0] if len(rel.parts) > 1 else rel.name
    return (top in SYNC_TOP_LEVEL) or (str(rel) in SYNC_SINGLE_FILES)


def select_files(repo: Path, since: str | None = None) -> list[Path]:
    """选出待同步文件。

    since 非空时，只同步 <since>..HEAD 之间发生变化的文件（最小增量部署），
    避免因为换行符差异或部署副本自带的本地文件而产生大面积无谓覆盖。
    无论哪种模式，都先按同步白名单收口，再走硬性排除。
    """
    if since:
        out = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", f"{since}..HEAD"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        rels = [Path(line.strip()) for line in out.stdout.splitlines() if line.strip()]
        rels = [rel for rel in rels if _in_sync_scope(rel)]
    else:
        rels = []
        for f in list_repo_files(repo):
            rel = Path(f)
            if _in_sync_scope(rel):
                rels.append(rel)
    return [rel for rel in rels if not _is_excluded(rel)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prune", action="store_true", help="（未实现：传入会直接报错退出，本脚本从不删除目标端文件）")
    ap.add_argument("--since", default=None,
                    help="只同步该提交之后发生变化的文件（最小增量部署，推荐）")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    dest = Path(args.dest).resolve()
    if not (repo / ".git").exists():
        print(f"[FATAL] {repo} 不是 Git 仓库")
        return 2
    if not dest.exists():
        print(f"[FATAL] 部署目录不存在：{dest}")
        return 2

    if args.prune:
        # 删除目标端文件风险极高（可能删掉用户本地资产），本脚本宁可显式拒绝，
        # 也不能静默 no-op 让使用者误以为已经清理过。
        print("[FATAL] --prune 未实现：本脚本从不删除目标端任何文件，也不会假装清理过。")
        print("        如需移除副本里的多余文件，请人工确认后手工删除，")
        print("        或先到 <dest>/_predeploy_backup_* 目录确认有备份再操作。")
        return 2

    files = select_files(repo, args.since)
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = dest / f"_predeploy_backup_{stamp}"

    added, updated, unchanged = [], [], []
    for rel in files:
        src = repo / rel
        dst = dest / rel
        if dst.exists():
            if _norm(src.read_bytes()) == _norm(dst.read_bytes()):
                unchanged.append(str(rel))
                continue
            updated.append(str(rel))
            if not args.dry_run:
                bak = backup_root / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
        else:
            added.append(str(rel))
        if not args.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    print(f"仓库       : {repo}")
    print(f"部署副本   : {dest}")
    print(f"同步文件数 : {len(files)}（新增 {len(added)} / 更新 {len(updated)} / 无变化 {len(unchanged)}）")
    if args.dry_run:
        print("[DRY-RUN] 未写入任何文件。")
        for f in added:
            print(f"  + {f}")
        for f in updated:
            print(f"  ~ {f}")
        return 0

    print(f"旧文件备份于: {backup_root}")
    for f in added:
        print(f"  + {f}")
    for f in updated:
        print(f"  ~ {f}")

    # 写入部署版本戳：这份副本究竟来自哪个提交，随时可查、可比对
    manifest = {
        "deployed_at": _dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "source_repo": str(repo),
        "source_commit": git(repo, "rev-parse", "HEAD").strip(),
        "source_commit_subject": git(repo, "show", "-s", "--format=%s", "HEAD").strip(),
        "source_commit_date": git(repo, "show", "-s", "--format=%cI", "HEAD").strip(),
        "source_dirty": bool(git(repo, "status", "--porcelain").strip()),
        "synced_since": args.since,
        "files": {
            str(rel): hashlib.sha256(_norm((dest / rel).read_bytes())).hexdigest()
            for rel in files
        },
    }
    stamp = dest / "DEPLOY-INFO.json"
    stamp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"部署版本戳  : {stamp}")
    print(f"来源提交    : {manifest['source_commit'][:12]} {manifest['source_commit_subject']}")
    if manifest["source_dirty"]:
        print("[WARN] 仓库工作区有未提交改动，部署的不是纯净提交！")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
