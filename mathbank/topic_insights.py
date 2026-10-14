"""专题学情：难度配比计算、失衡提示与触发规则。

阈值集中为模块常量，便于教师按自己的班级经验调整。
全部规则基于本地已有数据，确定、可解释、离线，不依赖 LLM。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from mathbank.topics import (
    DIFFICULTY_DEVIATION_TOLERANCE,
    DIFFICULTY_MIN_ITEMS,
    DIFFICULTY_ORDER,
    DIFFICULTY_PROFILES,
    LEVEL_LABEL,
    normalize_difficulty_target,
)

#: 学情触发阈值（班级教学经验值）。
CORRECT_RATE_HIGH = 0.40  # 低于该值判「高级」：大概率超纲或讲解缺失
CORRECT_RATE_MID = 0.60  # 低于该值判「中级」：跳一跳但需铺垫
CORRECT_RATE_MIN_SAMPLE = 10  # 样本量不足不触发（防小样本误判）
TIME_MULTIPLIER = 2.0  # 平均用时超过专题中位数该倍数才提示
TIME_MIN_SECONDS = 180  # 且绝对值不低于该秒数
TOPIC_LEVEL_HIGH_COUNT = 3  # 达到该数目的高级触发升格为专题级提示


def latest_stats(db: Session, question_id: int) -> dict | None:
    """读取口径集中在唯一的函数：一题一行的最近一条学情记录。"""

    row = db.execute(
        text(
            "SELECT correct_rate, avg_seconds, sample_size, note, recorded_at "
            "FROM question_usage_stats WHERE question_id = :q "
            "ORDER BY recorded_at DESC, id DESC LIMIT 1"
        ),
        {"q": int(question_id)},
    ).first()
    if row is None:
        return None
    return {
        "correct_rate": row[0],
        "avg_seconds": row[1],
        "sample_size": int(row[2] or 0),
        "note": row[3] or "",
        "recorded_at": row[4],
    }


def difficulty_balance_report(db: Session, topic_id: int) -> dict:
    """难度配比：实际占比 vs 目标配比，给出「按目标补 N 题」的可执行建议。"""

    topic = db.execute(
        text("SELECT topic_type FROM topics WHERE id = :t"), {"t": int(topic_id)}
    ).first()
    if topic is None:
        raise ValueError("专题不存在")
    topic_type = topic[0]
    # 目标配比按专题类型取默认档（DIFFICULTY_PROFILES），与 topics 表字段一一对应。
    target = normalize_difficulty_target(DIFFICULTY_PROFILES.get(topic_type))
    rows = db.execute(
        text(
            "SELECT q.difficulty AS difficulty, COUNT(*) AS n "
            "FROM topic_items ti JOIN questions q ON q.id = ti.question_id "
            "WHERE ti.topic_id = :t GROUP BY q.difficulty"
        ),
        {"t": int(topic_id)},
    ).fetchall()
    counts = {level: 0 for level in DIFFICULTY_ORDER}
    for difficulty, count in rows:
        level = str(difficulty or "").strip().lower()
        if level in counts:
            counts[level] = int(count)
    total = sum(counts.values())

    levels = []
    for level in DIFFICULTY_ORDER:
        actual = counts[level] / total if total else 0.0
        levels.append(
            {
                "level": level,
                "label": LEVEL_LABEL[level],
                "count": counts[level],
                "actual_ratio": round(actual, 4),
                "target_ratio": round(target[level], 4),
            }
        )

    warnings: list[dict] = []
    suggestions: list[dict] = []
    if total < DIFFICULTY_MIN_ITEMS:
        return {
            "total": total,
            "target": {level: round(target[level], 4) for level in DIFFICULTY_ORDER},
            "levels": levels,
            "warnings": warnings,
            "suggestions": suggestions,
            "suppressed_reason": f"题目不足 {DIFFICULTY_MIN_ITEMS} 道，暂不做配比提示",
        }

    for entry in levels:
        level = entry["level"]
        deviation = entry["actual_ratio"] - entry["target_ratio"]
        if entry["count"] == 0 and entry["target_ratio"] >= 0.20:
            warnings.append(
                {
                    "level": level,
                    "kind": "missing",
                    "message": (
                        f"无{entry['label']}（目标 "
                        f"{round(entry['target_ratio'] * 100)}%）"
                    ),
                }
            )
            needed = max(1, round(entry["target_ratio"] * total))
            suggestions.append({"level": level, "add": needed})
        elif deviation > DIFFICULTY_DEVIATION_TOLERANCE:
            warnings.append(
                {
                    "level": level,
                    "kind": "over",
                    "message": (
                        f"{entry['label']}占比 "
                        f"{round(entry['actual_ratio'] * 100)}%，高于目标 "
                        f"{round(entry['target_ratio'] * 100)}%"
                    ),
                }
            )
        elif deviation < -DIFFICULTY_DEVIATION_TOLERANCE:
            warnings.append(
                {
                    "level": level,
                    "kind": "under",
                    "message": (
                        f"{entry['label']}占比 "
                        f"{round(entry['actual_ratio'] * 100)}%，低于目标 "
                        f"{round(entry['target_ratio'] * 100)}%"
                    ),
                }
            )
            needed = max(1, round(-deviation * total))
            suggestions.append({"level": level, "add": needed})
    return {
        "total": total,
        "target": {level: round(target[level], 4) for level in DIFFICULTY_ORDER},
        "levels": levels,
        "warnings": warnings,
        "suggestions": suggestions,
    }


def topic_insights_report(db: Session, topic_id: int) -> dict:
    """学情触发：题目级（正确率/用时）+ 专题级汇总。无数据的题永不提示。"""

    rows = db.execute(
        text(
            "SELECT ti.position, ti.question_id, q.difficulty "
            "FROM topic_items ti JOIN questions q ON q.id = ti.question_id "
            "WHERE ti.topic_id = :t ORDER BY ti.position ASC"
        ),
        {"t": int(topic_id)},
    ).fetchall()
    entries: list[dict] = []
    high_count = 0
    for position, question_id, difficulty in rows:
        stats = latest_stats(db, question_id)
        if stats is None:
            continue  # 无数据的题永不提示
        seq = position + 1
        rate = stats["correct_rate"]
        seconds = stats["avg_seconds"]
        sample = stats["sample_size"]
        entry = {
            "question_id": question_id,
            "seq_num": seq,
            "difficulty": difficulty,
            "correct_rate": rate,
            "avg_seconds": seconds,
            "sample_size": sample,
            "triggers": [],
        }
        if rate is not None and sample >= CORRECT_RATE_MIN_SAMPLE:
            if rate < CORRECT_RATE_HIGH:
                entry["triggers"].append(
                    {
                        "level": "high",
                        "message": (
                            f"第 {seq} 题正确率 {round(rate * 100)}%"
                            f"（{sample} 人），建议替换或补充讲解"
                        ),
                    }
                )
                high_count += 1
            elif rate < CORRECT_RATE_MID:
                entry["triggers"].append(
                    {
                        "level": "mid",
                        "message": (
                            f"第 {seq} 题正确率 {round(rate * 100)}%，"
                            "建议调整顺序（前置基础题）或增加铺垫变式"
                        ),
                    }
                )
        entries.append(entry)

    # 用时触发需要专题中位数，单独一轮计算。
    timed = [entry for entry in entries if entry["avg_seconds"]]
    if timed:
        ordered = sorted(entry["avg_seconds"] for entry in timed)
        middle = len(ordered) // 2
        median = (
            ordered[middle]
            if len(ordered) % 2
            else (ordered[middle - 1] + ordered[middle]) / 2
        )
        for entry in timed:
            if (
                entry["avg_seconds"] > TIME_MIN_SECONDS
                and median > 0
                and entry["avg_seconds"] > median * TIME_MULTIPLIER
            ):
                entry["triggers"].append(
                    {
                        "level": "time",
                        "message": (
                            f"第 {entry['seq_num']} 题平均用时 "
                            f"{entry['avg_seconds']}s，约为本专题中位数 "
                            f"{round(median)}s 的 "
                            f"{round(entry['avg_seconds'] / median, 1)} 倍，"
                            "建议拆分问题链"
                        ),
                    }
                )

    triggered = [entry for entry in entries if entry["triggers"]]
    topic_level = None
    if high_count >= TOPIC_LEVEL_HIGH_COUNT:
        topic_level = {
            "level": "topic",
            "message": (
                f"本专题 {high_count} 题正确率低于 "
                f"{round(CORRECT_RATE_HIGH * 100)}%，整体偏难，"
                "建议提高基础题占比"
            ),
            "jump_to": "difficulty_balance",
        }
    return {
        "entries": triggered,
        "topic_level": topic_level,
        "thresholds": {
            "correct_rate_high": CORRECT_RATE_HIGH,
            "correct_rate_mid": CORRECT_RATE_MID,
            "correct_rate_min_sample": CORRECT_RATE_MIN_SAMPLE,
            "time_multiplier": TIME_MULTIPLIER,
            "time_min_seconds": TIME_MIN_SECONDS,
        },
    }
