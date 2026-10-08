#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本漂移体检：防止"仓库已更新、实际运行副本没更新"再次发生。

这是"题库看起来回退"的直接防线。它回答一个问题：
    **用户正在跑的那份程序，和 Git 仓库里的源码，是不是同一份？**

检查三件事：
    1. 部署副本的 DEPLOY-INFO.json 记录的来源提交，是否已经是仓库 HEAD；
       落后几个提交，落后者是哪几个（带时间，人对得上"我那天改的没生效"）。
    2. 部署副本磁盘上的运行时文件，是否与仓库工作区逐字节一致（忽略 CRLF/LF）。
    3. 部署副本的数据库 schema 版本，是否达到程序要求的 LATEST_SCHEMA_VERSION
       （取自 mathbank/db_migrations.py）；低于目标版本即为 FAIL，需要先跑
       tools/deploy/migrate_db.py。顺带报告题目数量，方便一眼看出"数据是不是还在"。

数据库全程以 sqlite 只读模式（mode=ro）打开，本脚本不做任何写操作。

退出码：0 = 无漂移；1 = 存在漂移（适合挂到启动器或定时任务里）。

用法：
    python tools/deploy/check_drift.py --repo <仓库目录> --dest <部署副本目录> [--json]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# 复用 sync_release 的排除规则：tests/、tools/ 等开发资产不进用户副本，
# 因此只改这些目录的提交不应被算作"版本漂移"。
from tools.deploy.sync_release import _is_excluded  # noqa: E402
from mathbank.db_migrations import LATEST_SCHEMA_VERSION  # noqa: E402


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return r.stdout.strip()


def norm(b: bytes) -> bytes:
    return b.replace(b"\r\n", b"\n")


def sha(b: bytes) -> str:
    import hashlib

    return hashlib.sha256(b).hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本消费")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    dest = Path(args.dest).resolve()
    report: dict[str, object] = {"repo": str(repo), "dest": str(dest)}
    problems: list[str] = []

    # ---- 1. 提交级漂移 ----
    head = git(repo, "rev-parse", "HEAD")
    stamp_file = dest / "DEPLOY-INFO.json"
    deployed_commit = None
    if stamp_file.exists():
        try:
            stamp = json.loads(stamp_file.read_text(encoding="utf-8"))
            deployed_commit = stamp.get("source_commit")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"DEPLOY-INFO.json 解析失败：{exc}")
            stamp = {}
    else:
        stamp = {}
        problems.append("部署副本缺少 DEPLOY-INFO.json（从未用 sync_release.py 部署过）")

    # 只把"含待部署文件"的提交算作漂移。纯开发资产（tests/、tools/、文档）
    # 本来就不进用户副本，把它们算进去会让守卫天天误报，最后被人忽略。
    behind: list[str] = []
    dev_only: list[str] = []
    if deployed_commit:
        revs = git(repo, "rev-list", f"{deployed_commit}..HEAD").split()
        for rev in revs:
            changed = [
                f for f in git(repo, "diff", "--name-only", f"{rev}~1", rev).splitlines() if f.strip()
            ]
            deployable = [f for f in changed if not _is_excluded(Path(f))]
            line = git(
                repo, "log", "-1", "--format=%h %ad %s", "--date=format:%m-%d %H:%M", rev
            ).strip()
            if deployable:
                behind.append(line)
            elif line:
                dev_only.append(line)

    report["deployed_commit"] = deployed_commit
    report["head_commit"] = head
    report["missing_commits"] = behind
    report["dev_only_commits"] = dev_only
    if behind:
        problems.append(f"部署副本落后仓库 {len(behind)} 个提交（这些改动在页面上根本不存在）")

    # ---- 2. 文件级漂移 ----
    dirty: list[str] = []
    missing: list[str] = []
    checked = 0
    for rel, recorded in (stamp.get("files") or {}).items():
        src = repo / rel
        dst = dest / rel
        if not dst.exists():
            missing.append(rel)
            continue
        if not src.exists():
            continue
        checked += 1
        if sha(norm(dst.read_bytes())) != recorded or sha(norm(src.read_bytes())) != sha(norm(dst.read_bytes())):
            dirty.append(rel)
    # 再检查一批固定关键文件，防止 DEPLOY-INFO.json 本身过期
    KEY = [
        "main.py", "mathbank/tags.py", "mathbank/database.py", "mathbank/db_migrations.py",
        "mathbank/resources/curriculums/A2019.json", "mathbank/resources/tag_schema.json",
        "static/index.html", "static/js/tags.js", "static/js/import.js", "static/js/editor.js",
    ]
    key_missing: list[str] = []
    key_dirty: list[str] = []
    for rel in KEY:
        src, dst = repo / rel, dest / rel
        if not dst.exists():
            key_missing.append(rel)
            continue
        if rel not in (stamp.get("files") or {}):
            checked += 1
            if src.exists() and sha(norm(src.read_bytes())) != sha(norm(dst.read_bytes())):
                key_dirty.append(rel)

    report["files_checked"] = checked
    report["files_dirty"] = sorted(set(dirty) | set(key_dirty))
    report["files_missing_in_dest"] = sorted(set(missing) | set(key_missing))
    if key_missing:
        problems.append(f"部署副本缺失关键文件：{', '.join(key_missing)}")
    if report["files_dirty"]:
        problems.append(f"{len(report['files_dirty'])} 个文件与仓库不一致：{', '.join(report['files_dirty'][:8])}")

    # ---- 3. 数据层（只读打开，本脚本绝不写库） ----
    # 目标版本以仓库迁移脚本为准，避免这里写死后与程序脱节。
    report["target_schema_version"] = LATEST_SCHEMA_VERSION
    dbs = sorted(dest.glob("*.db"))
    db_info = []
    for db in dbs:
        uri = "file:" + db.resolve().as_posix().replace("?", "%3f").replace("#", "%23") + "?mode=ro&uri=true"
        conn = sqlite3.connect(uri, uri=True)
        try:
            uv = int(conn.execute("PRAGMA user_version").fetchone()[0])
            n = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            has_tags = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_tags'"
            ).fetchone() is not None
        finally:
            conn.close()
        db_info.append({
            "db": db.name, "schema_version": uv, "questions": n, "has_question_tags": has_tags,
        })
    report["databases"] = db_info
    for info in db_info:
        if not info["has_question_tags"]:
            problems.append(
                f"{info['db']} 仍无 question_tags 表（schema v{info['schema_version']}），"
                f"多值标签无处存储 —— 请跑 migrate_db.py"
            )
        if info["schema_version"] < LATEST_SCHEMA_VERSION:
            problems.append(
                f"{info['db']} schema 版本 v{info['schema_version']} 低于程序要求的 "
                f"v{LATEST_SCHEMA_VERSION}（共 {info['questions']} 道题），"
                f"字段/索引可能缺失 —— 请先跑 tools/deploy/migrate_db.py 完成迁移再使用"
            )
        elif info["schema_version"] > LATEST_SCHEMA_VERSION:
            problems.append(
                f"{info['db']} schema 版本 v{info['schema_version']} 高于程序支持的 "
                f"v{LATEST_SCHEMA_VERSION}，请升级程序代码后再使用"
            )

    report["ok"] = not problems
    report["problems"] = problems

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if not problems else 1

    print("=" * 66)
    print("  版本漂移体检（仓库 vs 实际运行的部署副本）")
    print("=" * 66)
    print(f"  仓库 HEAD : {head[:12] if head else '?'}")
    print(f"  副本来源  : {(deployed_commit or '未知')[:12]}")
    if behind:
        print(f"\n  [漂移] 副本落后 {len(behind)} 个提交，以下改动未生效：")
        for line in behind:
            print(f"      - {line}")
    else:
        print("\n  [OK] 副本来源提交 == 仓库 HEAD（无待部署改动）")
    if dev_only:
        print(f"\n  以下 {len(dev_only)} 个提交只动开发资产（tests/、tools/ 等），无需部署，已忽略：")
        for line in dev_only:
            print(f"      · {line}")
    print(f"\n  文件比对：检查 {checked} 个，不一致 {len(report['files_dirty'])} 个，缺失 {len(report['files_missing_in_dest'])} 个")
    for f in report["files_dirty"][:10]:
        print(f"      ~ {f}")
    for f in report["files_missing_in_dest"][:10]:
        print(f"      ! {f}")
    print(f"\n  数据库（目标 schema v{LATEST_SCHEMA_VERSION}，只读打开）：")
    for info in db_info:
        verdict = "版本达标" if info["schema_version"] == LATEST_SCHEMA_VERSION else "版本不达标"
        print(f"      {info['db']}: schema v{info['schema_version']}（{verdict}），题目 {info['questions']} 道，"
              f"question_tags {'有' if info['has_question_tags'] else '无'}")
    print()
    if problems:
        print("  结论：存在漂移，页面看到的就是旧版本。")
        for p in problems:
            print(f"    ✗ {p}")
        print("\n  修复：")
        print(f"    python tools/deploy/sync_release.py --repo \"{repo}\" --dest \"{dest}\" --since {deployed_commit or 'HEAD~9'}")
        print(f"    \"{dest}\\python\\python.exe\" tools/deploy/migrate_db.py --project-root \"{dest}\"")
        return 1
    print("  结论：无漂移，实际运行的副本就是仓库里的最新版本。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
