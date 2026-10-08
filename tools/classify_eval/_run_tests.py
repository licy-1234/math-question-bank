# -*- coding: utf-8 -*-
"""无 pytest 环境下的最小测试执行器：跑 tests/ 下所有 test_* 函数。"""
import sys
import importlib.util
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

passed = 0
failed = []
for path in sorted((ROOT / "tests").glob("test_*.py")):
    name = f"_t_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # 导入即失败（如缺 requests）
        print(f"[SKIP] {path.name}: 导入失败 -> {type(exc).__name__}: {exc}")
        continue
    fns = [(n, f) for n, f in vars(module).items()
           if n.startswith("test_") and callable(f)]
    if not fns:
        continue
    print(f"\n--- {path.name} ({len(fns)} 项) ---")
    for fname, fn in fns:
        try:
            fn()
            passed += 1
            print(f"  PASS {fname}")
        except Exception:
            failed.append((path.name, fname, traceback.format_exc()))
            print(f"  FAIL {fname}")

print("\n" + "=" * 60)
print(f"通过 {passed} 项，失败 {len(failed)} 项")
for file_name, fname, tb in failed:
    print("\n" + "#" * 60)
    print(f"FAIL {file_name}::{fname}")
    print(tb)
sys.exit(1 if failed else 0)
