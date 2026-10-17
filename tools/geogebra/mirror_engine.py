#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GeoGebra 网页引擎本地镜像脚本（阶段一：工程准备）。

把 GeoGebra 网页引擎（web3d，构建 5.4.932.1）的运行时资源从官方 CDN 下载到
``static/lib/geogebra/<版本>/``，并写入 ``ENGINE-VERSION.json`` 版本戳，供
``static/js/ggb.js`` 的 ``PREFER_LOCAL_ENGINE`` 本地优先路径使用。

安全边界：
    - 公开仓库 = 公开再分发，GeoGebra 引擎的再分发许可尚未确认，因此本脚本下载的
      引擎文件一律不进 git（见 .gitignore）；本脚本只是本地工程准备工具，不改变
      默认运行行为（默认仍走 CDN）。
    - 下载后做字节级校验（主包 / nocache 大小 + 强名），校验不过以非 0 退出，
      避免把半截引擎写进镜像。

用法：
    python tools/geogebra/mirror_engine.py                       # 下载 + 校验 + 写版本戳
    python tools/geogebra/mirror_engine.py --dry-run             # 只打印计划，不联网、不写盘
    python tools/geogebra/mirror_engine.py --check               # 只校验已存在镜像，不发网络
    python tools/geogebra/mirror_engine.py --check --engine-dir <dir>   # 校验指定镜像根目录
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

VERSION = "5.4.932.1"
STRONG_NAME = "A235CC712983FA478D93F2A05A5A8D23"

CDN_APPS = "https://www.geogebra.org/apps/"
DEPLOYGGB_URL = CDN_APPS + "deployggb.js"

# 字节级校验基准：主包（GWT 强名 cache.js）与 web3d.nocache.js 的已知大小。
MAIN_PACKAGE_SIZE = 7_921_911
NOCACHE_SIZE = 5_455

TIMEOUT_SECONDS = 30
RETRIES = 1  # 失败后重试 1 次（即共 2 次尝试）
RETRY_DELAY_SECONDS = 1
USER_AGENT = "mathbank-geogebra-mirror/1.0"

# 相对 <engine-dir>/<VERSION>/ 的路径；deployggb.js 在 CDN 根 /apps/，
# 其余在 /apps/<VERSION>/ 下。deferredjs 的编号来自 web3d.nocache.js 的 manifest。
DEFERRED_JS = (5, 6, 7, 8, 9, 10, 15)
REL_PATHS = [
    "deployggb.js",
    "web3d/web3d.nocache.js",
    f"web3d/{STRONG_NAME}.cache.js",
    "web3d/js/properties_keys_zh-CN.js",
    *[f"web3d/deferredjs/{STRONG_NAME}/{n}.cache.js" for n in DEFERRED_JS],
    "css/bundles/simple-bundle.css",
    "css/bundles/bundle.css",
    "css/keyboard-styles.css",
    "css/fonts.css",
    "css/greek-font.css",
]


def remote_url(rel_path: str) -> str:
    if rel_path == "deployggb.js":
        return DEPLOYGGB_URL
    return CDN_APPS + VERSION + "/" + rel_path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_engine_dir() -> Path:
    return repo_root() / "static" / "lib" / "geogebra"


def version_dir(engine_dir: Path) -> Path:
    return engine_dir / VERSION


def _known_size(rel_path: str) -> int | None:
    if rel_path == f"web3d/{STRONG_NAME}.cache.js":
        return MAIN_PACKAGE_SIZE
    if rel_path == "web3d/web3d.nocache.js":
        return NOCACHE_SIZE
    return None


def check_files(engine_dir: Path) -> list[str]:
    """校验引擎文件本身（存在性 + 字节级），不含版本戳。返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    vdir = version_dir(engine_dir)
    for rel in REL_PATHS:
        p = vdir / rel
        if not p.exists():
            problems.append(f"缺少文件：{rel}")
            continue
        size = p.stat().st_size
        expected = _known_size(rel)
        if expected is not None and size != expected:
            problems.append(f"{rel} 字节数不符：{size} != {expected}")
        if rel == "web3d/web3d.nocache.js":
            try:
                content = p.read_text(encoding="utf-8", errors="replace")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{rel} 无法读取：{exc}")
                content = ""
            if STRONG_NAME not in content:
                problems.append(f"{rel} 内容缺少强名 {STRONG_NAME}")
    return problems


def check_stamp(engine_dir: Path) -> list[str]:
    """校验 ENGINE-VERSION.json 版本戳。返回问题清单（空 = 通过）。"""
    problems: list[str] = []
    stamp = engine_dir / "ENGINE-VERSION.json"
    if not stamp.exists():
        problems.append(f"缺少版本戳：{stamp}")
        return problems
    data = None
    try:
        data = json.loads(stamp.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"版本戳无法解析：{stamp}（{exc}）")
        return problems
    if data.get("version") != VERSION:
        problems.append(f"版本戳 version 不符：{data.get('version')!r} != {VERSION!r}")
    if data.get("strong_name") != STRONG_NAME:
        problems.append(f"版本戳 strong_name 不符：{data.get('strong_name')!r} != {STRONG_NAME!r}")
    return problems


def check_mirror(engine_dir: Path) -> list[str]:
    """校验已存在镜像的完整性与版本戳（供 --check 与 stage_engine 复用）。"""
    return check_files(engine_dir) + check_stamp(engine_dir)


def download(url: str, dest: Path) -> int:
    """带超时与重试地下载单个文件，原子写入，返回字节数。"""
    last_error: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                data = resp.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
            return len(data)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"下载失败：{url}")


def write_stamp(engine_dir: Path, sizes: dict[str, int]) -> Path:
    stamp = engine_dir / "ENGINE-VERSION.json"
    payload = {
        "version": VERSION,
        "strong_name": STRONG_NAME,
        "files": {rel: sizes[rel] for rel in REL_PATHS},
    }
    stamp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return stamp


def dry_run(engine_dir: Path) -> int:
    vdir = version_dir(engine_dir)
    print(f"镜像根   : {engine_dir}")
    print(f"版本目录 : {vdir}")
    print(f"将下载 {len(REL_PATHS)} 个文件（不联网、不写盘）：")
    known_total = 0
    for rel in REL_PATHS:
        known = _known_size(rel)
        if known is not None:
            known_total += known
            size_label = f"{known} B"
        else:
            size_label = "未知（未联网不探测）"
        print(f"  {rel}")
        print(f"      <- {remote_url(rel)}  [{size_label}]")
    print(f"已知体积合计（仅主包 + nocache）：{known_total} B；其余文件体积需实际下载后确定。")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="GeoGebra 网页引擎本地镜像")
    ap.add_argument("--check", action="store_true", help="只校验已存在镜像完整性 + 版本戳，不发网络请求")
    ap.add_argument("--dry-run", action="store_true", help="打印计划下载的文件与预计体积，不联网、不写盘")
    ap.add_argument("--engine-dir", default=None,
                    help=f"镜像根目录（默认 {default_engine_dir()}）")
    args = ap.parse_args(argv)

    engine_dir = Path(args.engine_dir).resolve() if args.engine_dir else default_engine_dir()

    if args.dry_run:
        return dry_run(engine_dir)

    if args.check:
        problems = check_mirror(engine_dir)
        if problems:
            print("[FAIL] 镜像校验未通过：", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1
        print(f"[OK] 镜像完整：{engine_dir}")
        return 0

    # 默认：真正下载
    vdir = version_dir(engine_dir)
    sizes: dict[str, int] = {}
    for i, rel in enumerate(REL_PATHS, start=1):
        url = remote_url(rel)
        dest = vdir / rel
        print(f"[{i}/{len(REL_PATHS)}] {url} -> {dest}")
        sizes[rel] = download(url, dest)

    problems = check_files(engine_dir)
    if problems:
        print("[FAIL] 下载后字节校验未通过：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 1

    stamp = write_stamp(engine_dir, sizes)
    print(f"[OK] 引擎镜像就绪：{vdir}")
    print(f"[OK] 版本戳：{stamp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
