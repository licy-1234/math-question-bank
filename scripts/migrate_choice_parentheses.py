#!/usr/bin/env python3
"""
⚠️⚠️⚠️  高危脚本警示：本脚本会改写题库中的「选择题题干正文」  ⚠️⚠️⚠️
==============================================================================
【运行前请务必读完】
1. 本脚本会直接 UPDATE questions 表的题干正文（抹掉选择题题干末尾用于填答的
   空括号），属于**不可逆的高风险批量写操作**。
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
    python -m scripts.migrate_choice_parentheses                    # 安全预演
    python -m scripts.migrate_choice_parentheses --db /path/x.db    # 指定库预演
    python -m scripts.migrate_choice_parentheses --apply            # 真改
    python -m scripts.migrate_choice_parentheses --apply --yes      # 真改不确认
==============================================================================
"""

import argparse
import datetime
import re
import sqlite3
import sys
from pathlib import Path

from mathbank.database import Question, QuestionFingerprint, SessionLocal
from mathbank.sync_helper import export_database_to_files


# 预览片段截断长度（字符），避免长题干刷屏
_PREVIEW_CHARS = 80


def clean_choice_stem_parentheses(text: str) -> str:
    """清理选择题题干末尾供填答用的全角/半角空括号并保证 $ 闭合"""
    if not text:
        return ""
    text = text.strip()
    pattern = r'(?:[\s\xa0\u3000]*[\(（]\s*\$?\s*(?:\\quad|\\qquad|\\hspace\{.*?\}|[\s\xa0\u3000_])*?\s*\$?\s*[\)）]\s*\$?[\s\xa0\u3000]*)+$'
    cleaned = re.sub(pattern, '', text).strip()
    cleaned = re.sub(r'\\paren\b', '', cleaned).strip()

    dollars = re.findall(r'(?<!\\)\$', cleaned)
    if len(dollars) % 2 != 0:
        cleaned += "$"

    return cleaned


def _clip(text, limit=_PREVIEW_CHARS):
    """把片段压成单行并在超长时截断，方便老师一眼看清改了什么。"""
    flat = " ".join((text or "").split())
    if len(flat) > limit:
        flat = flat[:limit] + " …(已截断)"
    return flat or "(空)"


def _resolve_target(db_path=None):
    """返回 (session_factory, 目标数据库 Path)。

    未显式指定 --db 时，完全沿用 mathbank.database 的默认绑定行为。
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
    print(f"⚠️  危险：即将真实修改 {changed_count} 道选择题的题干正文（写库操作）")
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


def run_migration(session_factory=None, apply=False):
    """扫描（并在 apply=True 时写回）选择题题干末尾残留的空括号。

    apply=False（默认）：纯预演，返回变更计划，不落库、不导出。
    apply=True：真正 UPDATE，并按原逻辑刷新派生指纹与导出文件。
    """
    print("=" * 65)
    mode = "真实写库 (--apply)" if apply else "DRY-RUN 预演（不会写入任何数据）"
    print(f"🧹 [Choice Parentheses] 模式：{mode}")
    print("=" * 65)

    factory = session_factory or SessionLocal
    changes = []
    session = factory()
    try:
        questions = session.query(Question).all()
        for q in questions:
            if q.question_type in ["single_choice", "multi_choice"] or (q.content and r"\begin{choices}" in q.content):
                content = q.content or ""
                if r"\begin{choices}" in content:
                    parts = content.split(r"\begin{choices}", 1)
                    stem_part = parts[0].strip()
                    cleaned_stem = clean_choice_stem_parentheses(stem_part)
                    if cleaned_stem != stem_part:
                        print(f"  [{'已改' if apply else '将改'}] 题目 #{q.id} (题干部分):")
                        print(f"    - 原: {_clip(stem_part)}")
                        print(f"    - 新: {_clip(cleaned_stem)}\n")
                        if apply:
                            q.content = cleaned_stem + "\n\\begin{choices}\n" + parts[1].strip()
                        changes.append(
                            {
                                "id": q.id,
                                "old": stem_part,
                                "new": cleaned_stem + "\n\\begin{choices}\n" + parts[1].strip(),
                            }
                        )
                else:
                    cleaned_content = clean_choice_stem_parentheses(content)
                    if cleaned_content != content.strip():
                        print(f"  [{'已改' if apply else '将改'}] 题目 #{q.id} (整题正文):")
                        print(f"    - 原: {_clip(content)}")
                        print(f"    - 新: {_clip(cleaned_content)}\n")
                        if apply:
                            q.content = cleaned_content
                        changes.append({"id": q.id, "old": content, "new": cleaned_content})

        if changes and apply:
            changed_ids = [item["id"] for item in changes]
            session.query(QuestionFingerprint).filter(
                QuestionFingerprint.question_id.in_(changed_ids)
            ).delete(synchronize_session=False)
            session.commit()
            print(f"✅ 清洗完成！共升级并净化了 {len(changes)} 道选择题题干中的残留末尾空括号。")

            # 同步导出备份文件
            print("正在同步更新 data_backup 备份文件...")
            export_database_to_files(session)
            print("备份文件同步更新完毕！")
        else:
            # dry-run 或未变更：显式回滚，确保一个字节都不写
            session.rollback()
            if changes:
                print(
                    f"✨ [DRY-RUN] 共需修改 {len(changes)} 道选择题，但未落库。"
                    "确认无误后请追加 --apply 执行。"
                )
            else:
                print("✨ 所有选择题题干均无残留末尾空括号，无需清洗。")

        print("=" * 65)
    except SystemExit:
        raise
    except Exception as e:
        session.rollback()
        print(f"❌ 迁移处理发生异常: {e}")
        return changes
    finally:
        session.close()

    return changes


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_choice_parentheses",
        description="选择题题干末尾空括号清洗（默认 dry-run，加 --apply 才真正写库）",
        epilog="⚠️ 本脚本会改写选择题题干；id=1..13、15 为人工确认版本，慎跑 --apply。",
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
    changes = run_migration(session_factory=session_factory, apply=False)
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
    run_migration(session_factory=session_factory, apply=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
