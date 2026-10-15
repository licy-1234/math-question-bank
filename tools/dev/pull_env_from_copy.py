#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""从"实际运行的部署副本"单向拉取 .env 到 Git 仓库（方案 A）。

方案 A：副本是配置的唯一事实来源。
    用户真正双击启动、真正被使用的是桌面上的便携副本：

        C:\\Users\\虫鱼\\Desktop\\MathBank-Windows-x64

    它里面的 .env 由应用「系统设置」端点持续回写维护（见 main.py 的设置保存逻辑），
    因此**只有这一份是当前有效、且始终最新的**。仓库里的 .env 只是开发/体检用的
    镜像，会随时间漂移（密钥过期、模型路由改版），从而出现"仓库密钥 401、副本 200"
    这种割裂。

本脚本把副本 .env 的内容**单向**复制进仓库 .env：
    - 只读源文件，只写目标文件：代码里不存在任何写回 ``--from`` 的路径。
    - 覆盖前把仓库现有 .env 备份为 ``.env.bak-<UTC 时间戳>``（受 .gitignore 的
      ``.env.*`` 保护，不会进版本库）。
    - 写入时在文件头加一行 ``# GENERATED-FROM: <源路径> @ <ISO时间>``；
      重跑时自动识别并替换旧头，**只保留一行**，不叠加。
    - 打印对比时一律脱敏：密钥只显示末 4 位 + 长度，模型串照常显示。
    - 幂等：内容已一致时不做无谓改写，只报告"已是最新"。

用法：
    python tools/dev/pull_env_from_copy.py                 # 拉取（默认路径）
    python tools/dev/pull_env_from_copy.py --dry-run       # 只看差异，不写文件
    python tools/dev/pull_env_from_copy.py --check         # 不一致时退出码非 0（供体检调用）
    python tools/dev/pull_env_from_copy.py --from <源> --to <目标> --backup-dir <备份目录>

退出码：
    0 = 成功 / 已是最新 / （--check 时）内容一致
    1 = （--check 时）内容不一致
    2 = 致命错误（源缺失/为空/缺键、目标=源、目标或备份目录落在副本内等）
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

# 让本模块可被 check_drift.py 复用其"纯解析/脱敏"函数，同时不把 PIL 等重依赖
# 拉进体检路径：安全写入（原子写 + owner-only 权限）在真正写文件时才惰性导入。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

# ---- 常量 ---------------------------------------------------------------

# 用户实际运行的副本根目录。本脚本对它可以"读"，绝不"写"。
COPY_ROOT = Path("C:/Users/虫鱼/Desktop/MathBank-Windows-x64")

DEFAULT_FROM = COPY_ROOT / ".env"
DEFAULT_TO = Path(__file__).resolve().parents[2] / ".env"

# 副本 .env 当前应包含的键集合（17 个）。缺任一键都视为源不完整，拒绝写半份配置。
EXPECTED_KEYS = (
    "DEEPSEEK_API_KEY",
    "DEEPSEEK_OCR_MODEL",
    "SILICONFLOW_API_KEY",
    "ALI_BAILIAN_API_KEY",
    "ZHONGZHAN_GPT_API_KEY",
    "ZHONGZHAN_GPT_BASE_URL",
    "ZHONGZHAN_GPT_OCR_MODEL",
    "ZHONGZHAN_CLAUDE_API_KEY",
    "ZHONGZHAN_CLAUDE_BASE_URL",
    "ZHONGZHAN_CLAUDE_OCR_MODEL",
    "OCR_PREFER_ENGINE",
    "SILICONFLOW_OCR_MODEL",
    "ALI_BAILIAN_OCR_MODEL",
    "PREFER_SOLVE_MODEL",
    "PREFER_PARSE_MODEL",
    "PREFER_CLASSIFY_MODEL",
    "PREFER_DRAW_MODEL",
)

HEADER_PREFIX = "# GENERATED-FROM:"

# 视为"含密"的键：打印时只显示末 4 位 + 长度。
_SENSITIVE_MARKERS = ("KEY", "SECRET", "TOKEN", "PASSWORD")


# ---- 纯函数：解析 / 校验 / 脱敏 / 比对 ----------------------------------


def is_sensitive_key(key: str) -> bool:
    """密钥类键（含 KEY/SECRET/TOKEN/PASSWORD）需要脱敏。"""

    upper = str(key).upper()
    return any(marker in upper for marker in _SENSITIVE_MARKERS)


def mask_value(key: str, value: str) -> str:
    """把键值脱敏后返回可安全打印的字符串。"""

    text = "" if value is None else str(value)
    if not is_sensitive_key(key):
        return text if text else "(空)"
    if not text:
        return "(空)"
    tail = text[-4:] if len(text) >= 4 else text
    return f"…{tail}（长度 {len(text)}）"


def parse_env_assignments(text: str) -> dict[str, str]:
    """把 .env 文本解析为 {键: 值}；忽略注释、空行与非赋值行。"""

    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _sep, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        result[key] = value.strip()
    return result


def missing_expected_keys(text: str) -> list[str]:
    """返回源文本里缺失的预期键（按 EXPECTED_KEYS 顺序）。"""

    present = parse_env_assignments(text)
    return [key for key in EXPECTED_KEYS if key not in present]


def strip_generated_header(text: str) -> tuple[str, int]:
    """去掉文件头部的 ``# GENERATED-FROM:`` 注释行。

    返回 (剩余正文, 头行数)。会连续吞掉所有历史叠加的头行，
    从而保证重跑时旧头被替换、而不会越叠越多（头行数 > 1 即需归一化）。
    """

    lines = text.splitlines()
    index = 0
    header_count = 0
    while index < len(lines) and lines[index].lstrip().startswith(HEADER_PREFIX):
        header_count += 1
        index += 1
    body_lines = lines[index:]
    # 头与正文之间若有一个空行，一并吞掉（只吞一个）。
    if body_lines and body_lines[0].strip() == "":
        body_lines = body_lines[1:]
    return "\n".join(body_lines), header_count


def build_generated_header(source: Path, when: _dt.datetime) -> str:
    """生成单行文件头，标注来源与拉取时间（UTC）。"""

    return f"{HEADER_PREFIX} {source.as_posix()} @ {when.isoformat()}"


def _normalize_body(text: str) -> str:
    """忽略换行符差异与首尾空行，便于幂等判断。"""

    return text.replace("\r\n", "\n").strip("\n")


def diff_values(source_assign: dict[str, str], dest_assign: dict[str, str]) -> dict[str, list]:
    """给出 新增键 / 删除键 / 取值变化键（均脱敏展示）。"""

    added = sorted(set(source_assign) - set(dest_assign))
    removed = sorted(set(dest_assign) - set(source_assign))
    changed = []
    for key in sorted(set(source_assign) & set(dest_assign)):
        if source_assign[key] != dest_assign[key]:
            changed.append(
                {
                    "key": key,
                    "copy": mask_value(key, source_assign[key]),
                    "repo": mask_value(key, dest_assign[key]),
                }
            )
    return {"added": added, "removed": removed, "changed": changed}


def build_comparison(source_text: str, dest_text: str) -> dict:
    """汇总源/目标差异，用于打印与幂等判断（全部脱敏）。"""

    source_body = _normalize_body(source_text)
    dest_body, header_count = strip_generated_header(dest_text)
    dest_body = _normalize_body(dest_body)
    return {
        "source_body": source_body,
        "dest_body": dest_body,
        "header_count": header_count,
        "header_present": header_count > 0,
        "identical": source_body == dest_body,
        "diff": diff_values(
            parse_env_assignments(source_text),
            parse_env_assignments(dest_text),
        ),
    }


# ---- 写向约束（单向保证 / 只读副本） ------------------------------------


def _assert_write_allowed(target: Path, source: Path) -> None:
    """确保目标可写：既不是源本身，也不落在用户副本内。"""

    resolved_target = target.resolve()
    if resolved_target == source.resolve():
        raise ValueError("拒绝：目标文件与源文件相同（单向拉取不允许写回源）。")
    if resolved_target == COPY_ROOT.resolve() or COPY_ROOT.resolve() in resolved_target.parents:
        raise ValueError(
            f"拒绝：目标落在用户副本内（本脚本对副本只读）：{resolved_target}"
        )


def _assert_backup_dir_allowed(backup_dir: Path) -> None:
    resolved = backup_dir.resolve()
    if resolved == COPY_ROOT.resolve() or COPY_ROOT.resolve() in resolved.parents:
        raise ValueError(f"拒绝：备份目录落在用户副本内（本脚本对副本只读）：{resolved}")


# ---- 主流程 -------------------------------------------------------------


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_private_text(path: Path, text: str) -> None:
    """惰性导入仓库的安全写入，避免模块导入期连带 PIL 等重依赖。"""

    from mathbank.asset_security import write_private_text_atomic

    write_private_text_atomic(path, text)


def _print_diff(diff: dict) -> None:
    if diff["added"]:
        print(f"  + 副本新增键（仓库将补入）：{', '.join(diff['added'])}")
    if diff["removed"]:
        print(f"  - 仓库多余键（副本没有，将被移除）：{', '.join(diff['removed'])}")
    for item in diff["changed"]:
        print(f"  ~ {item['key']}: 仓库 {item['repo']}  ->  副本 {item['copy']}")


def run(
    source: Path,
    dest: Path,
    *,
    backup_dir: Path,
    dry_run: bool = False,
    check: bool = False,
    now: _dt.datetime | None = None,
) -> int:
    """执行（或仅检查）单向拉取，返回退出码。"""

    when = now or _dt.datetime.now(_dt.timezone.utc)

    try:
        _assert_write_allowed(dest, source)
        _assert_backup_dir_allowed(backup_dir)
    except ValueError as exc:
        print(f"[FATAL] {exc}")
        return 2

    if not source.exists():
        print(f"[FATAL] 源 .env 不存在：{source}")
        return 2

    source_text = _read_text(source)
    if not source_text.strip():
        print(f"[FATAL] 源 .env 为空，拒绝写入半份配置：{source}")
        return 2

    missing = missing_expected_keys(source_text)
    if missing:
        print(f"[FATAL] 源 .env 缺少 {len(missing)} 个预期键，拒绝写入半份配置：")
        for key in missing:
            print(f"    - {key}")
        return 2

    dest_text = _read_text(dest) if dest.exists() else ""
    comparison = build_comparison(source_text, dest_text)
    identical = comparison["identical"]

    print("=" * 66)
    print("  从副本单向拉取 .env（副本 = 配置唯一事实来源）")
    print("=" * 66)
    print(f"  源（副本，只读）: {source}")
    print(f"  目标（仓库）    : {dest}")
    print(f"  键数量          : 源 {len(parse_env_assignments(source_text))} 个"
          f"（预期 {len(EXPECTED_KEYS)} 个）")

    if identical and comparison["header_count"] == 1:
        print("\n  差异：无。仓库 .env 已与副本一致（已是最新）。")
        if check:
            return 0
        print("  动作：内容一致，跳过写入（幂等）。")
        return 0

    if not dest.exists():
        print("\n  目标仓库 .env 不存在，将新建。")
    elif identical:
        print(
            "\n  差异：取值完全一致，但 GENERATED-FROM 头缺失或叠加（"
            f"当前 {comparison['header_count']} 行），将归一化为单行文件头。"
        )
    else:
        print("\n  差异（脱敏）：")
        _print_diff(comparison["diff"])

    if check:
        print("\n  结论：仓库 .env 与副本不一致，建议执行：")
        print("    python tools/dev/pull_env_from_copy.py")
        return 1

    if dry_run:
        print("\n[DRY-RUN] 未写入任何文件。")
        return 0

    # 覆盖前备份现有仓库 .env（不含新头），时间戳用 UTC，文件名受 .env.* 忽略保护。
    if dest.exists():
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f".env.bak-{when.strftime('%Y%m%dT%H%M%SZ')}"
        _write_private_text(backup_path, dest_text)
        print(f"\n  旧配置已备份 : {backup_path}（含旧密钥，请勿提交/打印）")

    header = build_generated_header(source, when)
    new_text = header + "\n" + comparison["source_body"].rstrip("\n") + "\n"
    _write_private_text(dest, new_text)
    print(f"  已写入       : {dest}")
    print(f"  文件头       : {header}")
    print("\n  完成。仓库 .env 现与副本一致（不含真实密钥的副本外泄风险由 .gitignore 兜底）。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="从部署副本单向拉取 .env 到仓库")
    parser.add_argument("--from", dest="source", default=str(DEFAULT_FROM),
                        help=f"源 .env（副本，只读），默认 {DEFAULT_FROM}")
    parser.add_argument("--to", dest="dest", default=str(DEFAULT_TO),
                        help=f"目标 .env（仓库），默认 {DEFAULT_TO}")
    parser.add_argument("--dry-run", action="store_true", help="只看差异，不写文件")
    parser.add_argument("--check", action="store_true",
                        help="仅检查一致性；不一致时退出码为 1（供体检调用）")
    parser.add_argument("--backup-dir", default=None,
                        help="备份目录，默认与目标 .env 同目录（仓库根）")
    args = parser.parse_args(argv)

    dest = Path(args.dest).expanduser()
    backup_dir = Path(args.backup_dir).expanduser() if args.backup_dir else dest.parent

    return run(
        Path(args.source).expanduser(),
        dest,
        backup_dir=backup_dir,
        dry_run=args.dry_run,
        check=args.check,
    )


if __name__ == "__main__":
    raise SystemExit(main())
