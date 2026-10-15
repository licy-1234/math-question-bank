#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""版本漂移体检：防止"仓库已更新、实际运行副本没更新"再次发生。

这是"题库看起来回退"的直接防线。它回答一个问题：
    **用户正在跑的那份程序，和 Git 仓库里的源码，是不是同一份？**

检查六件事：
    1. 部署副本的 DEPLOY-INFO.json 记录的来源提交，是否已经是仓库 HEAD；
       落后几个提交，落后者是哪几个（带时间，人对得上"我那天改的没生效"）。
    2. 部署副本磁盘上的运行时文件，是否与仓库工作区逐字节一致（忽略 CRLF/LF）。
    3. 部署副本的数据库 schema 版本，是否达到程序要求的 LATEST_SCHEMA_VERSION
       （取自 mathbank/db_migrations.py）；低于目标版本即为 FAIL，需要先跑
       tools/deploy/migrate_db.py。顺带报告题目数量，方便一眼看出"数据是不是还在"。
    4. schema v12 结构体检：question_relations 表必须在位（新关联单轨），
       且旧题组列 questions.association_group_id 必须已清空（无残留双轨）。
    5. schema v13 结构体检：专题工坊六表（topics/topic_groups/topic_items/
       topic_versions/topic_links/question_usage_stats）必须在位。专题是用户
       原创教学设计，刻意不纳入自愈白名单——缺表不会静默重建，必须在体检中
       暴露，引导从 data_backup 恢复备份。
    6. .env 配置只读体检：比对仓库与副本 .env 的**键名集合 / 密钥末 4 位 /
       模型串**。⚠️ 这两份 .env 本来就该不同（PRESERVE_NAMES 刻意保护 .env
       不同步，副本才是配置唯一事实来源），所以默认**只告警、不影响退出码**；
       只有显式 `--strict-env` 才把差异计入退出码。输出会指出"仓库这份密钥
       很可能已失效/N 个键不同/仓库 .env 比副本旧 N 天"，并提示跑
       `python tools/dev/pull_env_from_copy.py` 从副本单向同步。

数据库全程以 sqlite 只读模式（mode=ro）打开，本脚本不做任何写操作。

退出码：0 = 无漂移；1 = 存在漂移（适合挂到启动器或定时任务里）。
        `--strict-env` 时，.env 配置差异也会让退出码变为 1。

用法：
    python tools/deploy/check_drift.py --repo <仓库目录> --dest <部署副本目录> [--json] [--strict-env]
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

# 复用 pull 工具的"纯解析/脱敏"函数（无重依赖），保证两边对密钥末 4 位、
# 模型串的展示口径一致。
from tools.dev.pull_env_from_copy import (  # noqa: E402
    is_sensitive_key,
    mask_value,
    parse_env_assignments,
)


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


def env_drift(repo: Path, dest: Path) -> dict:
    """只读比对仓库 .env 与副本 .env（键集合 / 密钥末 4 位 / 模型串）。

    ⚠️ 关键设计约束：.env 在副本与仓库之间**本来就该不同**——sync_release 的
    PRESERVE_NAMES 刻意保护 .env 不同步。因此本检查只产出**告警**，默认不进入
    退出码；只有显式 --strict-env 才把它计入。check_drift 退出码 0 是既定交付门禁，
    不能被配置差异打破。

    全程只读，且输出一律脱敏（密钥只显示末 4 位 + 长度）。
    """

    repo_env = repo / ".env"
    dest_env = dest / ".env"
    result: dict = {
        "repo_env": str(repo_env),
        "dest_env": str(dest_env),
        "repo_env_present": repo_env.exists(),
        "dest_env_present": dest_env.exists(),
        "keys_only_in_repo": [],
        "keys_only_in_dest": [],
        "changed_values": [],
        "repo_env_age_days_vs_copy": None,
        "warnings": [],
    }

    if not dest_env.exists():
        result["warnings"].append(
            "部署副本缺少 .env（副本本应是配置唯一事实来源，请确认是否被误删）"
        )
        return result
    if not repo_env.exists():
        result["warnings"].append(
            "仓库缺少 .env（副本是唯一事实来源；可跑 "
            "python tools/dev/pull_env_from_copy.py 从副本拉取）"
        )
        return result

    repo_assign = parse_env_assignments(repo_env.read_text(encoding="utf-8"))
    dest_assign = parse_env_assignments(dest_env.read_text(encoding="utf-8"))

    only_repo = sorted(set(repo_assign) - set(dest_assign))
    only_dest = sorted(set(dest_assign) - set(repo_assign))
    changed = [
        {
            "key": key,
            "repo": mask_value(key, repo_assign[key]),
            "copy": mask_value(key, dest_assign[key]),
        }
        for key in sorted(set(repo_assign) & set(dest_assign))
        if repo_assign[key] != dest_assign[key]
    ]

    result["keys_only_in_repo"] = only_repo
    result["keys_only_in_dest"] = only_dest
    result["changed_values"] = changed

    try:
        delta = dest_env.stat().st_mtime - repo_env.stat().st_mtime
        result["repo_env_age_days_vs_copy"] = round(delta / 86400.0, 1)
    except OSError:
        pass

    if only_repo or only_dest:
        parts = []
        if only_repo:
            parts.append(f"仓库多出 {', '.join(only_repo)}")
        if only_dest:
            parts.append(f"仓库缺少 {', '.join(only_dest)}")
        result["warnings"].append(
            f"仓库 .env 与副本键名集合不一致（{'；'.join(parts)}）"
        )

    if changed:
        sensitive = [c for c in changed if is_sensitive_key(c["key"])]
        models = [c for c in changed if not is_sensitive_key(c["key"])]
        detail = "；".join(f"{c['key']}={c['repo']} vs 副本 {c['copy']}" for c in changed)
        message = f"仓库 .env 有 {len(changed)} 个键取值与副本不同（{detail}）"
        if sensitive:
            message += (
                f"。其中 {len(sensitive)} 个是 API 密钥，"
                "副本为唯一有效来源，仓库这份很可能已失效"
            )
        if models:
            message += f"；另有 {len(models)} 个模型路由键漂移"
        result["warnings"].append(message)

    age = result["repo_env_age_days_vs_copy"]
    if age is not None and age > 0.5:
        result["warnings"].append(f"仓库 .env 比副本旧约 {age:g} 天")

    if result["warnings"]:
        result["warnings"].append(
            "同步建议：python tools/dev/pull_env_from_copy.py"
            "（先加 --check 看差异；副本是配置唯一事实来源）"
        )
    return result


def resolve_exit_code(
    problems: list[str], env_warnings: list[str], *, strict_env: bool = False
) -> int:
    """决定退出码。

    .env 差异默认是**告警级**：副本与仓库的 .env 本就应不同，不能被它打破
    "退出码 0 = 可交付"的既定门禁。只有 --strict-env 才把 .env 差异计入。
    """

    if problems:
        return 1
    if strict_env and env_warnings:
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--json", action="store_true", help="以 JSON 输出，便于脚本消费")
    ap.add_argument(
        "--strict-env",
        action="store_true",
        help="把 .env 配置差异也计入退出码（默认只告警：副本与仓库的 .env 本就"
             "应当不同，不能被配置差异打破交付门禁）",
    )
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
        "static/js/ocr.js",
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
            # schema v12：新关联单轨（question_relations）+ 旧题组列必须已清空。
            has_relations = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='question_relations'"
            ).fetchone() is not None
            relation_rows = (
                int(conn.execute("SELECT COUNT(*) FROM question_relations").fetchone()[0])
                if has_relations else 0
            )
            group_columns = {
                row[1] for row in conn.execute('PRAGMA table_info("questions")').fetchall()
            }
            legacy_group_rows = 0
            if "association_group_id" in group_columns:
                legacy_group_rows = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM questions "
                        "WHERE association_group_id IS NOT NULL AND association_group_id <> ''"
                    ).fetchone()[0]
                )
            # schema v13：专题工坊六表必须在位（原创内容，缺表不静默重建）。
            topic_table_names = (
                "topics", "topic_groups", "topic_items",
                "topic_versions", "topic_links", "question_usage_stats",
            )
            missing_topic_tables = [
                name for name in topic_table_names
                if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                ).fetchone() is None
            ]
        finally:
            conn.close()
        db_info.append({
            "db": db.name, "schema_version": uv, "questions": n,
            "has_question_tags": has_tags,
            "has_question_relations": has_relations,
            "question_relations": relation_rows,
            "legacy_association_group_rows": legacy_group_rows,
            "missing_topic_tables": missing_topic_tables,
        })
    report["databases"] = db_info
    for info in db_info:
        if not info["has_question_tags"]:
            problems.append(
                f"{info['db']} 仍无 question_tags 表（schema v{info['schema_version']}），"
                f"多值标签无处存储 —— 请跑 migrate_db.py"
            )
        if not info["has_question_relations"]:
            problems.append(
                f"{info['db']} 仍无 question_relations 表（schema v{info['schema_version']}），"
                f"新关联无处存储 —— 请跑 migrate_db.py 升级到 v{LATEST_SCHEMA_VERSION}"
            )
        if info["legacy_association_group_rows"]:
            problems.append(
                f"{info['db']} 仍有 {info['legacy_association_group_rows']} 道题目带着旧的 "
                f"association_group_id（旧题组未折叠），会与新关联双轨不同步 —— "
                f"请跑 migrate_db.py 完成 v{LATEST_SCHEMA_VERSION} 迁移"
            )
        if info["missing_topic_tables"]:
            problems.append(
                f"{info['db']} 缺少专题工坊表：{', '.join(info['missing_topic_tables'])}"
                f"（schema v{info['schema_version']}，程序要求 v{LATEST_SCHEMA_VERSION}）—— "
                f"专题属用户原创教学设计，不纳入自愈白名单，缺表不会静默重建；"
                f"请先从 data_backup 恢复备份，或跑 migrate_db.py 完成 v{LATEST_SCHEMA_VERSION} 迁移"
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

    # ---- 4. .env 配置只读体检（告警级，默认不影响退出码） ----
    # 副本与仓库的 .env 本来就该不同，因此这里的差异只作告警；只有 --strict-env
    # 才把它计入退出码，避免配置差异打破"退出码 0 = 可交付"的既定门禁。
    env_report = env_drift(repo, dest)
    env_warnings = env_report["warnings"]
    report["env"] = env_report
    report["env_ok"] = not env_warnings
    report["env_warnings"] = env_warnings

    exit_code = resolve_exit_code(problems, env_warnings, strict_env=args.strict_env)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return exit_code

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
        topic_note = (
            "专题六表齐全" if not info["missing_topic_tables"]
            else f"缺专题表：{', '.join(info['missing_topic_tables'])}"
        )
        print(f"      {info['db']}: schema v{info['schema_version']}（{verdict}），题目 {info['questions']} 道，"
              f"question_tags {'有' if info['has_question_tags'] else '无'}，"
              f"question_relations {'有' if info['has_question_relations'] else '无'}"
              f"（{info['question_relations']} 条），旧题组残留 {info['legacy_association_group_rows']} 道，"
              f"{topic_note}")
    print()
    # ---- .env 配置体检（只读、告警级） ----
    print("  .env 配置体检（只读；副本与仓库的 .env 本就应不同，默认仅告警）：")
    if not env_warnings:
        print("      [OK] 仓库 .env 与副本的键集合 / 密钥末 4 位 / 模型串一致。")
    else:
        for warning in env_warnings:
            print(f"      ⚠ {warning}")
    print()
    if problems:
        print("  结论：存在漂移，页面看到的就是旧版本。")
        for p in problems:
            print(f"    ✗ {p}")
        print("\n  修复：")
        print(f"    python tools/deploy/sync_release.py --repo \"{repo}\" --dest \"{dest}\" --since {deployed_commit or 'HEAD~9'}")
        print(f"    \"{dest}\\python\\python.exe\" tools/deploy/migrate_db.py --project-root \"{dest}\"")
        return exit_code
    if env_warnings and args.strict_env:
        print("  结论：无版本漂移；但 --strict-env 下 .env 配置存在差异（退出码 1）。")
        return exit_code
    print("  结论：无漂移，实际运行的副本就是仓库里的最新版本。")
    if env_warnings:
        print("  提示：仓库 .env 与副本不同——这是预期行为（副本是配置唯一事实来源），未计入退出码。")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
