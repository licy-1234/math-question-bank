#!/usr/bin/env python3
"""
⚠️⚠️⚠️  高危脚本警示：本脚本会改写题库的「正文 / 答案 / 解析」  ⚠️⚠️⚠️
==============================================================================
【运行前请务必读完】
1. 本脚本会直接 UPDATE questions 表的三个字段（content、answer_markdown、
   review），替换 Word 数学私有字符（PUA），属于**不可逆的高风险批量写操作**。
2. **不带 --apply 直接运行（本脚本的命令行入口）是安全的 dry-run（预演）**：
   只扫描并打印"将会修改哪几道题、改前片段、改后片段"，一行都不写。
   ⚠️ 注意：直接import调用 `migrate_database_word_math_private_chars()` 为兼容
   历史行为，默认仍是 apply=True。命令行入口永远先 dry-run，再按需 --apply。
3. 只有**显式**传入 `--apply` 才会真正落库；落库前脚本会：
     (a) 先把目标 .db 复制成一份 `math_question_bank.db.bak_<YYYYmmdd_HHMMSS>`
         备份，**备份失败就直接中止**（绝不在没有备份的情况下改数据）；
     (b) 打印出备份文件的完整路径，方便随时找回；
     (c) 要求你手动输入 yes 做二次确认（可用 --yes 跳过交互）。
4. **当前题库里 14 道题（id=1..13、15）是高中数学老师逐条手工编辑确认的最终
   版本，除非老师本人明确要求，绝对不要对它们执行 --apply。**

用法：
    python -m scripts.migrate_word_math_private_chars                 # 安全预演
    python -m scripts.migrate_word_math_private_chars --db /p/x.db    # 指定库预演
    python -m scripts.migrate_word_math_private_chars --apply         # 真改
    python -m scripts.migrate_word_math_private_chars --apply --yes   # 真改不确认
==============================================================================

Repair verified Word math Private Use Area glyphs in stored questions.
"""

import argparse
import datetime
import sqlite3
import sys
from pathlib import Path

from mathbank.database import Question, QuestionFingerprint, SessionLocal
from mathbank.omml_helper import (
    find_unknown_word_math_private_characters,
    normalize_known_word_math_private_characters,
    normalize_word_linear_latex_boundaries,
)
from mathbank.sync_helper import export_database_to_files


QUESTION_TEXT_FIELDS = ("content", "answer_markdown", "review")

# 预览片段截断长度（字符），避免长题干刷屏
_PREVIEW_CHARS = 80


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
    print(f"⚠️  危险：即将真实修改 {changed_count} 道题的正文 / 答案 / 解析（写库操作）")
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


def migrate_database_word_math_private_chars(session_factory=None, apply=True) -> dict:
    """Replace verified PUA glyphs and report unknown ones without guessing.

    apply=True（默认，兼容历史调用方）：真正落库并按原逻辑刷新派生指纹与导出。
    apply=False：纯预演，返回同样的结果字典，但一个字节都不写。
    """
    print("=" * 65)
    mode = "真实写库 (--apply)" if apply else "DRY-RUN 预演（不会写入任何数据）"
    print(f"🔤 [Word Math PUA] 模式：{mode}")
    print("=" * 65)

    factory = session_factory or SessionLocal
    db = factory()
    changed_questions = []
    changed_fields = 0
    unknown_by_question = {}
    try:
        questions = db.query(Question).order_by(Question.id.asc()).all()
        for question in questions:
            question_changed = False
            unknown_labels = set()
            for field in QUESTION_TEXT_FIELDS:
                old_value = getattr(question, field, "") or ""
                unknown_labels.update(find_unknown_word_math_private_characters(old_value))
                new_value = normalize_word_linear_latex_boundaries(
                    normalize_known_word_math_private_characters(old_value)
                )
                if new_value != old_value:
                    print(f"  [{'已改' if apply else '将改'}] 题目 #{question.id} 字段 `{field}`:")
                    print(f"    - 原: {_clip(old_value)}")
                    print(f"    - 新: {_clip(new_value)}\n")
                    if apply:
                        setattr(question, field, new_value)
                    changed_fields += 1
                    question_changed = True
            if question_changed:
                changed_questions.append(question.id)
            if unknown_labels:
                unknown_by_question[question.id] = sorted(unknown_labels)

        if changed_questions and apply:
            db.query(QuestionFingerprint).filter(
                QuestionFingerprint.question_id.in_(changed_questions)
            ).delete(synchronize_session=False)
            db.commit()
            export_database_to_files(db)
        else:
            # dry-run 或未变更：显式回滚，确保一个字节都不写
            db.rollback()

        result = {
            "scanned": len(questions),
            "changed_questions": changed_questions,
            "changed_fields": changed_fields,
            "unknown_by_question": unknown_by_question,
        }
        print(
            f"[Word Math PUA] 扫描 {result['scanned']} 道题，"
            f"{'修复' if apply else '预判需修复'} {len(changed_questions)} 道题的 "
            f"{changed_fields} 个字段。"
        )
        if changed_questions:
            print("[Word Math PUA] 涉及题目 ID：" + ", ".join(map(str, changed_questions)))
        if unknown_by_question:
            print("[Word Math PUA] 仍有未知私用字符，未进行猜测替换：")
            for question_id, labels in unknown_by_question.items():
                print(f"  - 题目 #{question_id}: {', '.join(labels)}")
        if changed_questions and not apply:
            print("✨ [DRY-RUN] 以上为预览，未落库。确认无误后请追加 --apply 执行。")
        print("=" * 65)
        return result
    except SystemExit:
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="scripts.migrate_word_math_private_chars",
        description="Word 数学私有字符修复（默认 dry-run，加 --apply 才真正写库）",
        epilog="⚠️ 本脚本会改写正文/答案/解析；id=1..13、15 为人工确认版本，慎跑 --apply。",
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
    plan = migrate_database_word_math_private_chars(
        session_factory=session_factory, apply=False
    )
    if not plan["changed_questions"]:
        return 0

    if not args.apply:
        print("\n💡 这是 dry-run 预览。若确认要写入，请追加 --apply 参数重新运行。")
        return 0

    # 第二步：先备份，备份不成功绝不继续
    _backup_database(db_path)

    # 第三步：二次确认
    if not args.yes and not _confirm_real_write(len(plan["changed_questions"])):
        return 1

    # 第四步：真正写库
    migrate_database_word_math_private_chars(session_factory=session_factory, apply=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"[Word Math PUA] 迁移失败：{exc}")
        sys.exit(1)
