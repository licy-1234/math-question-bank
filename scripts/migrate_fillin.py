#!/usr/bin/env python3
"""
⚠️⚠️⚠️  高危脚本警示：本脚本会改写题库中的「题目正文」  ⚠️⚠️⚠️
==============================================================================
【运行前请务必读完】
1. 本脚本会直接 UPDATE questions 表的题目正文（把旧下划线格式改写为 \\fillin
   宏），属于**不可逆的高风险批量写操作**。
2. **不带 --apply 直接运行是安全的 dry-run（预演）**：只扫描并打印"将会修改
   哪几道题、改前片段、改后片段"，一行都不写。请放心用这种方式先自查。
3. 只有**显式**传入 `--apply` 才会真正落库；落库前脚本会：
     (a) 先把目标 .db 复制成一份 `math_question_bank.db.bak_<YYYYmmdd_HHMMSS>`
         备份，**备份失败就直接中止**（绝不在没有备份的情况下改数据）；
     (b) 打印出备份文件的完整路径，方便随时找回；
     (c) 要求你手动输入 yes 做二次确认（可用 --yes 跳过交互）。
4. **当前题库里 14 道题（id=1..13、15）是高中数学老师逐条手工编辑确认的最终
   版本，除非老师本人明确要求，绝对不要对它们执行 --apply。**

用法：
    python -m scripts.migrate_fillin                       # 安全预演（默认）
    python -m scripts.migrate_fillin --db /path/to/x.db    # 指定数据库预演
    python -m scripts.migrate_fillin --apply               # 真改（会备份+确认）
    python -m scripts.migrate_fillin --apply --yes         # 真改且不交互确认
==============================================================================

migrate_fillin.py - 历史题库填空题下划线批量升级为 \\fillin 宏工具
------------------------------------------------------------------
该脚本用于扫描本地 SQLite 数据库中所有已入库的题目，自动将题干中的旧下划线格式
（例如 ______、\\underline{...}、\\fillin[...]）一律规范化为最纯粹干净的 \\fillin 宏，
并在执行完成后同步更新 JSON 数据备份与 AI 专属只读题库文件。
"""

import argparse
import ast
import datetime
import re
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

from mathbank.database import SessionLocal, Question, QuestionFingerprint
from mathbank.sync_helper import export_database_to_files


# 预览片段截断长度（字符），避免长题干刷屏
_PREVIEW_CHARS = 80


@lru_cache(maxsize=1)
def _load_normalize_fillin_macro():
    """从 main.py 源码里取出 normalize_fillin_macro，而**不 import main**。

    直接 ``import main`` 会执行 main.py 的模块级代码：获取运行锁、调用
    ``init_db()`` 直接初始化/迁移**默认连接的真实数据库**、加载元数据、跑启动
    诊断……对一个"只想预览"的迁移脚本来说，这些副作用既危险又不可接受。

    因此这里只把 main.py 中该函数的源码抽出来单独编译执行，既保持与 main.py
    的单一事实来源一致，又完全不会触发任何启动副作用。抽不到就报错，绝不静默
    退化成"什么都不改"。
    """
    source_path = Path(__file__).resolve().parent.parent / "main.py"
    source = source_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(source_path))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "normalize_fillin_macro":
            namespace = {"re": re}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_path), "exec"), namespace)
            return namespace["normalize_fillin_macro"]
    raise RuntimeError(f"未能从 {source_path} 中找到 normalize_fillin_macro 函数，已终止。")


def normalize_fillin_macro(text):
    """统一调用入口：内部委托给 main.py 中的同名实现。"""
    return _load_normalize_fillin_macro()(text)


def _clip(text, limit=_PREVIEW_CHARS):
    """把片段压成单行并在超长时截断，方便老师一眼看清改了什么。"""
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit] + " …(已截断)"
    return flat or "(空)"


def _resolve_target(db_path=None):
    """返回 (session_factory, 目标数据库 Path)。

    未显式指定 --db 时，完全沿用 mathbank.database 的默认绑定行为；
    指定时只为本次运行单独建一个 engine，不污染其它模块。
    """
    if db_path:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker

        target = Path(db_path).resolve()
        engine = create_engine(
            f"sqlite:///{target.as_posix()}", connect_args={"check_same_thread": False}
        )
        return sessionmaker(autocommit=False, autoflush=False, bind=engine), target

    from mathbank import database as _database

    return _database.SessionLocal, Path(_database.engine.url.database).resolve()


def _backup_database(db_path):
    """先用 SQLite online backup 做一份一致的快照，失败则直接中止。"""
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    target = db_path.parent / f"{db_path.name}.bak_{stamp}"
    print(f"\n🗂  正在备份数据库 → {target}")
    source = target_conn = None
    try:
        source = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&uri=true", uri=True)
        target_conn = sqlite3.connect(target.as_posix())
        source.backup(target_conn)
    except Exception as exc:  # noqa: BLE001 - 任何原因都必须在写库前中止
        print(f"❌ 备份失败，已中止，未对数据库做任何修改：{exc}")
        raise SystemExit(2)
    finally:
        if target_conn is not None:
            target_conn.close()
        if source is not None:
            source.close()

    if not target.is_file() or target.stat().st_size <= 0:
        print("❌ 备份文件无效（不存在或为空），已中止，未对数据库做任何修改。")
        raise SystemExit(2)

    print(f"✅ 备份完成（{target.stat().st_size} 字节）：{target}")
    print("   ↑ 如需回滚，把上面这个文件复制回原数据库路径即可。")
    return target


def _confirm_real_write(changed_count):
    """落库前的二次确认；非 tty / EOF 一律按「取消」处理。"""
    print()
    print("!" * 72)
    print(f"⚠️  危险：即将真实修改 {changed_count} 道题的正文（写库操作）")
    print("⚠️  题库中 id=1..13、15 为老师人工确认的最终版本，请确认你真的要改。")
    print("!" * 72)
    try:
        answer = input("确认请输入 yes（直接回车或其它输入均视为取消）: ").strip().lower()
    except EOFError:
        answer = ""
    if answer != "yes":
        print("⏹  已取消，未对数据库做任何修改。")
        return False
    return True


def migrate_database_fillin(session_factory=None, apply=False):
    """扫描（并在 apply=True 时写回）需要迁移的填空题下划线。

    apply=False（默认）：纯预演，只返回一个变更计划，不落库、不导出。
    apply=True：真正 UPDATE，并按原逻辑刷新派生指纹与导出文件。
    """
    print("=" * 65)
    mode = "真实写库 (--apply)" if apply else "DRY-RUN 预演（不会写入任何数据）"
    print(f"🚀 [Fillin Migration] 模式：{mode}")
    print("=" * 65)

    factory = session_factory or SessionLocal
    db = factory()
    changes = []
    try:
        questions = db.query(Question).all()
        total_questions = len(questions)

        for q in questions:
            if not q.content:
                continue

            old_content = q.content
            new_content = normalize_fillin_macro(old_content)

            if new_content != old_content:
                print(f"  [{'已改' if apply else '将改'}] 题目 #{q.id} ({q.question_type or '未知题型'}):")
                print(f"    - 原: {_clip(old_content)}")
                print(f"    - 新: {_clip(new_content)}\n")
                if apply:
                    q.content = new_content
                changes.append({"id": q.id, "old": old_content, "new": new_content})

        if changes and apply:
            changed_ids = [item["id"] for item in changes]
            db.query(QuestionFingerprint).filter(
                QuestionFingerprint.question_id.in_(changed_ids)
            ).delete(synchronize_session=False)
            db.commit()
            print(f"✅ 成功升级 {len(changes)} / {total_questions} 道题目的下划线为纯净 \\fillin 宏！")

            print("\n🔄 正在同步导出最新数据至 JSON 备份文件与 AI 专属题库...")
            export_database_to_files(db)
            print("🎉 自动同步完成！(JSON 备份与 Markdown 题库已刷新)")
        else:
            # dry-run 或未变更：显式回滚，确保一个字节都不写
            db.rollback()
            if changes:
                print(
                    f"✨ [DRY-RUN] 共需修改 {len(changes)} / {total_questions} 道题，"
                    "但未落库。确认无误后请追加 --apply 执行。"
                )
            else:
                print(f"✨ 数据库中所有 {total_questions} 道题目均已是纯净的 \\fillin 格式，无需迁移。")

        print("=" * 65)
    except SystemExit:
        raise
    except Exception as e:
        db.rollback()
        print(f"❌ 迁移升级过程中发生异常: {str(e)}")
        sys.exit(1)
    finally:
        db.close()

    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_fillin",
        description="填空题下划线批量升级（默认 dry-run，加 --apply 才真正写库）",
        epilog="⚠️ 本脚本会改写题目正文；id=1..13、15 为人工确认版本，慎跑 --apply。",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="真正执行 UPDATE（默认只做 dry-run 预览）；执行前会自动备份数据库",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="跳过交互二次确认（仅供自动化流水线使用）",
    )
    parser.add_argument(
        "--db",
        dest="db_path",
        default=None,
        help="显式指定要操作的 SQLite 数据库路径（默认沿用 mathbank.database 的默认库）",
    )
    args = parser.parse_args(argv)

    session_factory, db_path = _resolve_target(args.db_path)
    print(f"🎯 目标数据库: {db_path}")
    if not db_path.is_file():
        print(f"❌ 目标数据库不存在：{db_path}（拒绝新建空库）")
        return 2

    # 第一步：无论什么模式都先跑一次 dry-run 预览
    changes = migrate_database_fillin(session_factory=session_factory, apply=False)
    if not changes:
        return 0

    if not args.apply:
        print("\n💡 这是 dry-run 预览。若确认要写入，请追加 --apply 参数重新运行。")
        return 0

    # 第二步：先备份，备份不成功绝不继续
    _backup_database(db_path)

    # 第三步：二次确认
    if not args.yes and not _confirm_real_write(len(changes)):
        return 1

    # 第四步：真正写库
    migrate_database_fillin(session_factory=session_factory, apply=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
