#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""定位"实际运行的部署副本"对应 Git 仓库中的哪一个提交（只读）。

原理：对部署副本的若干关键文件计算 sha256，与仓库历史中每个提交的同名
文件字节逐一比对，找出内容完全一致的最新提交；若没有整体一致，则逐文件
报告最近一次内容匹配的提交。

比对前会把两侧字节都做 CRLF→LF 归一化（与 sync_release._norm 一致）：
Windows 部署副本是 CRLF、git blob 是 LF，不归一化会让本脚本对每个文件都
得出"仓库历史中无匹配"的假阴性结论。

用法：
    python tools/deploy/match_deployed_commit.py <deploy_dir> [--repo <repo_dir>]
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

KEY_FILES = [
    "main.py",
    "mathbank/database.py",
    "mathbank/db_migrations.py",
    "mathbank/curriculums.py",
    "mathbank/question_types.py",
    "mathbank/prompts.py",
    "static/index.html",
    "static/js/import.js",
    "static/js/editor.js",
    "static/js/api.js",
    "static/css/app.css",
]


def _norm(b: bytes) -> bytes:
    """忽略 CRLF/LF 差异，避免把"仅换行符不同"误报为内容变更。

    与 tools/deploy/sync_release.py 的 _norm 保持一致。
    """
    return b.replace(b"\r\n", b"\n")


def sha256_bytes(b: bytes) -> str:
    """归一化行尾后再算 sha256；调用方须保证两侧都过本函数。"""
    return hashlib.sha256(_norm(b)).hexdigest()


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout


def show_bytes(repo: Path, rev: str, rel: str):
    r = subprocess.run(["git", "-C", str(repo), "show", f"{rev}:{rel}"], capture_output=True)
    return r.stdout if r.returncode == 0 else None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    deploy_dir = Path(sys.argv[1])
    repo = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[2] == "--repo" else Path(__file__).resolve().parents[2]

    deploy_fp: dict[str, str] = {}
    for rel in KEY_FILES:
        p = deploy_dir / rel
        deploy_fp[rel] = sha256_bytes(p.read_bytes()) if p.exists() else "MISSING"

    print("=== 部署副本关键文件指纹（CRLF 归一化后） ===")
    for rel in KEY_FILES:
        print(f"  {deploy_fp[rel][:16]}  {rel}")

    revs = git(repo, "rev-list", "--max-count=300", "HEAD").split()
    print(f"\n=== 比对仓库最近 {len(revs)} 个提交 ===")

    for rev in revs:
        matched = 0
        total = 0
        for rel in KEY_FILES:
            if deploy_fp.get(rel) == "MISSING":
                continue
            blob = show_bytes(repo, rev, rel)
            if blob is None:
                continue
            total += 1
            # blob 同样经 sha256_bytes → 已做 CRLF 归一化
            if sha256_bytes(blob) == deploy_fp[rel]:
                matched += 1
        if total and matched == total:
            info = git(repo, "show", "-s", "--format=%H%n%ad%n%s", "--date=iso", rev).strip()
            print(f"\n[OK] 部署副本代码 == 提交\n{info}\n完全匹配文件数：{matched}/{total}")
            return 0

    print("未找到整体一致的提交，逐文件定位最近匹配：")
    for rel in KEY_FILES:
        if deploy_fp.get(rel) == "MISSING":
            print(f"  {rel}: 部署副本缺失该文件")
            continue
        found = None
        for rev in revs:
            blob = show_bytes(repo, rev, rel)
            if blob is not None and sha256_bytes(blob) == deploy_fp[rel]:
                found = rev
                break
        if found:
            info = git(repo, "show", "-s", "--format=%h %ad %s", "--date=iso", found).strip()
        else:
            info = "仓库历史中无匹配（属于本地未提交的改动）"
        print(f"  {rel}: {info}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
