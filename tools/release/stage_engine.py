#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把本地 GeoGebra 引擎镜像复制到发布副本（非 git 通道）。

公开仓库里没有引擎文件（再分发许可未确认，见 .gitignore），发布时由本脚本把本地
``static/lib/geogebra/``（含引擎 + ENGINE-VERSION.json）整体复制到 ``--dest`` 指定的
副本根目录，使发布包带上本地引擎，前端 ``PREFER_LOCAL_ENGINE`` 打开后可离线加载。

安全约束：
    - 复制前先跑 ``mirror_engine.check_mirror``，镜像缺失/损坏一律拒绝，避免发布半截引擎；
    - 只写 ``<dest>/static/lib/geogebra/``，不碰其它任何文件。

用法：
    python tools/release/stage_engine.py --dest <发布副本根目录>
    python tools/release/stage_engine.py --dest <发布副本根目录> --engine-dir <本地镜像根>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_mirror():
    """mirror_engine 与 stage_engine 分属不同目录，运行时把 tools/geogebra 挂到 sys.path。"""
    tools_geogebra = repo_root() / "tools" / "geogebra"
    if str(tools_geogebra) not in sys.path:
        sys.path.insert(0, str(tools_geogebra))
    import mirror_engine  # noqa: PLC0415
    return mirror_engine


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="复制本地 GeoGebra 引擎镜像到发布副本")
    ap.add_argument("--dest", required=True,
                    help="发布副本根目录（引擎复制到 <dest>/static/lib/geogebra/）")
    ap.add_argument("--engine-dir", default=None,
                    help="本地镜像根目录（默认 <仓库>/static/lib/geogebra/）")
    args = ap.parse_args(argv)

    mirror = _load_mirror()
    engine_src = Path(args.engine_dir).resolve() if args.engine_dir else mirror.default_engine_dir()

    if not engine_src.exists():
        print(f"[FATAL] 本地引擎镜像不存在：{engine_src}", file=sys.stderr)
        print("        请先运行 python tools/geogebra/mirror_engine.py 下载引擎。", file=sys.stderr)
        return 2

    problems = mirror.check_mirror(engine_src)
    if problems:
        print("[FATAL] 本地引擎镜像不完整，拒绝发布：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    dest = Path(args.dest).resolve() / "static" / "lib" / "geogebra"
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(engine_src, dest)
    print(f"[OK] 已复制引擎镜像：{engine_src} -> {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
