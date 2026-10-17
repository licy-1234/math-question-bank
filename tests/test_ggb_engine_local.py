"""GeoGebra 引擎本地化·阶段一 的工程准备回归测试。

阶段一只做「本地优先 + CDN 兜底」的加载路径准备，默认关闭；本文件只做源级断言
加离线脚本行为校验，绝不打 geogebra.org。
"""

from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
STATIC_JS = ROOT / "static" / "js"
GGB_SOURCE = (STATIC_JS / "ggb.js").read_text(encoding="utf-8")


def test_prefer_local_engine_switch_is_default_off():
    assert "const PREFER_LOCAL_ENGINE = false;" in GGB_SOURCE, \
        "PREFER_LOCAL_ENGINE 必须默认关闭，关闭时与既有 CDN 行为逐字节一致"
    assert "const ENGINE_VERSION = '5.4.932.1';" in GGB_SOURCE
    assert "const GGB_LOCAL_BASE = '/static/lib/geogebra/' + ENGINE_VERSION;" in GGB_SOURCE


def test_resolve_functions_exist_and_default_to_cdn():
    assert "function resolveCodebase" in GGB_SOURCE
    assert "function resolveDeployggbSrc" in GGB_SOURCE
    # 默认分支返回 CDN 常量（源级断言足够，不必真跑 DOM）。
    assert "PREFER_LOCAL_ENGINE ? (GGB_LOCAL_BASE + '/web3d') : GGB_CODEBASE" in GGB_SOURCE
    assert "PREFER_LOCAL_ENGINE ? (GGB_LOCAL_BASE + '/deployggb.js') : DEPLOYGGB_SRC" in GGB_SOURCE
    # 三个调用点都改走解析函数，不再继续写死 CDN 常量。
    assert "script.src = resolveDeployggbSrc();" in GGB_SOURCE
    assert "const base = resolveCodebase().replace(/\\/+$/, '');" in GGB_SOURCE
    assert "applet.setHTML5Codebase(resolveCodebase());" in GGB_SOURCE
    # CDN 常量保留不动（前后端同步测试与 CDN 兜底都依赖）。
    assert "const GGB_CODEBASE = 'https://www.geogebra.org/apps/5.4.932.1/web3d';" in GGB_SOURCE
    assert "const DEPLOYGGB_SRC = 'https://www.geogebra.org/apps/deployggb.js';" in GGB_SOURCE


def test_gitignore_ignores_the_engine_mirror():
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "static/lib/geogebra/" in gitignore
    assert "!static/lib/geogebra/README.md" in gitignore


def test_engine_readme_placeholder_exists():
    readme = ROOT / "static" / "lib" / "geogebra" / "README.md"
    assert readme.exists(), "README.md 是镜像目录里唯一允许入库的占位文件"


def test_mirror_check_fails_on_missing_engine(tmp_path):
    engine_dir = tmp_path / "static" / "lib" / "geogebra"
    engine_dir.mkdir(parents=True)
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools" / "geogebra" / "mirror_engine.py"),
            "--check",
            "--engine-dir", str(engine_dir),
        ],
        capture_output=True, text=True, cwd=ROOT, timeout=60,
    )
    assert proc.returncode != 0, f"空镜像目录 --check 应返回非 0：\n{proc.stdout}{proc.stderr}"
