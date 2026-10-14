"""专题工坊领域层：白名单校验、嵌套环检测、快照与恢复、有序组合。

纯领域逻辑，不 import main（避免循环依赖）；HTTP 路由层负责把 ValueError
翻译成 400、把 RuntimeError 翻译成 500。
"""

from __future__ import annotations

import json
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from mathbank.database import (
    Topic,
    TopicGroup,
    TopicItem,
    TopicLink,
    TopicVersion,
)

#: 10 种预设专题类型（与用户设想一一对应）。
TOPIC_TYPES = (
    "knowledge", "method", "question_type", "error_prone", "variant",
    "multi_solution", "cross_chapter", "exam_oriented", "wrong_retry", "micro",
)
TOPIC_TYPE_LABELS = {
    "knowledge": "知识专题",
    "method": "方法专题",
    "question_type": "题型专题",
    "error_prone": "易错专题",
    "variant": "变式专题",
    "multi_solution": "一题多解",
    "cross_chapter": "跨章节专题",
    "exam_oriented": "应试专题",
    "wrong_retry": "错题再练",
    "micro": "微专题",
}

#: 专题间关联类型（前置 / 进阶 / 同类对比 / 母题变式 / 派生来源）+ custom: 逃生舱。
TOPIC_LINK_CODES = ("prereq", "progression", "similar", "parent_variant", "derived_from")
TOPIC_LINK_LABELS = {
    "prereq": "前置基础",
    "progression": "基础进阶",
    "similar": "同类对比",
    "parent_variant": "母题变式",
    "derived_from": "派生来源",
}

#: 组织五维视图（同一份 items 数据的 5 种前端视图）。
TOPIC_VIEW_MODES = ("linear", "grouped", "tree", "tag", "relation")
TOPIC_VIEW_MODE_LABELS = {
    "linear": "线性排序",
    "grouped": "分组排列",
    "tree": "树状嵌套",
    "tag": "标签视图",
    "relation": "关系视图",
}

#: 6 个使用场景（只记录不打分支）。
TOPIC_USAGE_SCENES = ("prepare", "lecture", "exercise", "research", "exam_review")
TOPIC_USAGE_SCENE_LABELS = {
    "prepare": "备课",
    "lecture": "授课",
    "exercise": "练习",
    "research": "教研",
    "exam_review": "考前复盘",
}

TOPIC_STATUSES = ("draft", "active", "archived")

TOPIC_MAX_DEPTH = 4
TOPIC_MAX_ITEMS = 300
TOPIC_LINK_CODE_MAX_LENGTH = 60
RELATION_CUSTOM_PREFIX = "custom:"


def _expanding_ids(name: str = "ids"):
    """IN :ids 需要 expanding bindparam（text() 不会自动展开元组）。"""

    return bindparam(name, expanding=True)

DIFFICULTY_ORDER = {"easy": 0, "medium": 1, "hard": 2}
LEVEL_LABEL = {"easy": "基础题", "medium": "中档题", "hard": "难题"}
DIFFICULTY_PROFILES = {
    "knowledge": {"easy": .30, "medium": .50, "hard": .20},
    "method": {"easy": .30, "medium": .50, "hard": .20},
    "question_type": {"easy": .25, "medium": .50, "hard": .25},
    "error_prone": {"easy": .40, "medium": .40, "hard": .20},
    "variant": {"easy": .25, "medium": .50, "hard": .25},
    "multi_solution": {"easy": .20, "medium": .50, "hard": .30},
    "cross_chapter": {"easy": .20, "medium": .45, "hard": .35},
    "exam_oriented": {"easy": .20, "medium": .50, "hard": .30},
    "wrong_retry": {"easy": .50, "medium": .35, "hard": .15},
    "micro": {"easy": .20, "medium": .45, "hard": .35},
}

#: 难度配比偏差超过该比例才提示（防噪音）。
DIFFICULTY_DEVIATION_TOLERANCE = 0.15
#: 总数少于该值不提示配比（防噪音）。
DIFFICULTY_MIN_ITEMS = 5


def validate_topic_type(value: str) -> str:
    code = str(value or "").strip()
    if code not in TOPIC_TYPES:
        raise ValueError(f"专题类型必须是：{' / '.join(TOPIC_TYPES)}")
    return code


def validate_topic_link_code(value: str) -> str:
    """白名单 + custom: 逃生舱（照 question_relations 的 relation_code 惯例）。"""

    code = str(value or "").strip()
    if code in TOPIC_LINK_CODES:
        return code
    if code.startswith(RELATION_CUSTOM_PREFIX) and len(code) > len(RELATION_CUSTOM_PREFIX):
        return code[:TOPIC_LINK_CODE_MAX_LENGTH]
    raise ValueError(
        f"专题关联类型必须是：{' / '.join(TOPIC_LINK_CODES)}（或 custom: 开头自定义）"
    )


def validate_topic_view_mode(value: str) -> str:
    code = str(value or "").strip()
    if code not in TOPIC_VIEW_MODES:
        raise ValueError(f"视图模式必须是：{' / '.join(TOPIC_VIEW_MODES)}")
    return code


def validate_topic_status(value: str) -> str:
    code = str(value or "").strip()
    if code not in TOPIC_STATUSES:
        raise ValueError(f"专题状态必须是：{' / '.join(TOPIC_STATUSES)}")
    return code


def ensure_no_topic_cycle(session: Session, topic_id: int, new_parent_id) -> None:
    """设置父专题前调用：沿 parent 链上行，遇自身或超深即拒绝。"""

    if new_parent_id is None:
        return
    new_parent_id = int(new_parent_id)
    topic_id = int(topic_id)
    if new_parent_id == topic_id:
        raise ValueError("不能将自己设为父专题")
    cursor, depth = new_parent_id, 0
    while cursor is not None:
        if cursor == topic_id:
            raise ValueError("会形成环：目标父专题是本专题的后代")
        cursor = session.execute(
            text("SELECT parent_topic_id FROM topics WHERE id = :i"), {"i": cursor}
        ).scalar()
        depth += 1
        if depth > TOPIC_MAX_DEPTH:
            raise ValueError(f"专题嵌套层级过深（上限 {TOPIC_MAX_DEPTH} 层）")


def normalize_difficulty_target(value) -> dict:
    """归一化到 easy+medium+hard = 1（对齐 normalize_sub_knowledge 房规）。"""

    raw = value if isinstance(value, dict) else {}
    picked = {level: max(0.0, float(raw.get(level) or 0.0)) for level in DIFFICULTY_ORDER}
    total = sum(picked.values())
    if total <= 0:
        return dict(DIFFICULTY_PROFILES["knowledge"])
    return {level: picked[level] / total for level in DIFFICULTY_ORDER}


def build_topic_snapshot(topic: Topic, items) -> dict:
    """自包含快照：主题元信息 + 分组 + 题目项（恢复时整体替换）。"""

    return {
        "title": topic.title,
        "topic_type": topic.topic_type,
        "description": topic.description or "",
        "view_mode": topic.view_mode or "linear",
        "groups": [
            {"id": g.id, "title": g.title, "position": g.position, "note": g.note}
            for g in sorted(topic.groups, key=lambda g: (g.position, g.id))
        ],
        "items": [
            {
                "question_id": it.question_id,
                "group_id": it.group_id,
                "position": it.position,
                "note": it.note,
            }
            for it in sorted(items, key=lambda it: it.position)
        ],
    }


def snapshot_diff_summary(previous: dict | None, current: dict) -> str:
    """与上一版快照对比，生成人读摘要，如「+3 题 / -1 题 / 重排 / 改批注」。"""

    if not previous:
        return "创建专题"
    parts: list[str] = []
    old_items = {it["question_id"]: it for it in previous.get("items", [])}
    new_items = {it["question_id"]: it for it in current.get("items", [])}
    added = len(set(new_items) - set(old_items))
    removed = len(set(old_items) - set(new_items))
    if added:
        parts.append(f"+{added} 题")
    if removed:
        parts.append(f"-{removed} 题")
    shared = set(old_items) & set(new_items)
    if any(old_items[q]["position"] != new_items[q]["position"] for q in shared):
        parts.append("重排")
    if any(old_items[q].get("group_id") != new_items[q].get("group_id") for q in shared):
        parts.append("调整分组")
    if any(old_items[q].get("note", "") != new_items[q].get("note", "") for q in shared):
        parts.append("改批注")
    for field, label in (("title", "改标题"), ("description", "改导语"), ("view_mode", "改视图")):
        if previous.get(field) != current.get(field):
            parts.append(label)
    return " / ".join(parts) if parts else "无实质变化"


def reorder_topic_items(session: Session, topic_id: int, ordered_ids: list[int]) -> None:
    """两段式重排单事务（不用 SQL 增量位移，避开唯一约束瞬态冲突）。

    UNIQUE(topic_id, position) + CHECK(position>=0) 双重约束下，逐行直接落终位
    会在第一行就撞上尚未改写的旧位；故先整批写 10000+ 偏移位避让，flush 后落终位。
    """

    items = session.query(TopicItem).filter(TopicItem.topic_id == topic_id).all()
    by_question = {it.question_id: it for it in items}
    ordered_ids = [int(value) for value in ordered_ids]
    if len(ordered_ids) != len(by_question) or set(ordered_ids) != set(by_question):
        raise ValueError("重排列表与专题内题目不一致")
    if len(ordered_ids) > TOPIC_MAX_ITEMS:
        raise ValueError(f"单个专题题目数上限 {TOPIC_MAX_ITEMS}")
    offset = 10000
    for position, question_id in enumerate(ordered_ids):  # 第一段：全体避让
        by_question[question_id].position = offset + position
    session.flush()
    for position, question_id in enumerate(ordered_ids):  # 第二段：落终位
        by_question[question_id].position = position
    session.flush()


def compact_item_positions(session: Session, topic_id: int) -> None:
    """把专题内 items 的 position 重写为 0 基连续（移出题目后调用）。"""

    items = (
        session.query(TopicItem)
        .filter(TopicItem.topic_id == topic_id)
        .order_by(TopicItem.position.asc(), TopicItem.id.asc())
        .all()
    )
    offset = 10000
    for index, item in enumerate(items):
        item.position = offset + index
    session.flush()
    for index, item in enumerate(items):
        item.position = index
    session.flush()


def compact_group_positions(session: Session, topic_id: int) -> None:
    """把专题内 groups 的 position 重写为 0 基连续。"""

    groups = (
        session.query(TopicGroup)
        .filter(TopicGroup.topic_id == topic_id)
        .order_by(TopicGroup.position.asc(), TopicGroup.id.asc())
        .all()
    )
    offset = 10000
    for index, group in enumerate(groups):
        group.position = offset + index
    session.flush()
    for index, group in enumerate(groups):
        group.position = index
    session.flush()


def reorder_topic_groups(session: Session, topic_id: int, ordered_ids: list[int]) -> None:
    """分组全量重排：与 reorder_topic_items 同样的两段式写位单事务。"""

    groups = session.query(TopicGroup).filter(TopicGroup.topic_id == topic_id).all()
    by_id = {group.id: group for group in groups}
    ordered_ids = [int(value) for value in ordered_ids]
    if len(ordered_ids) != len(by_id) or set(ordered_ids) != set(by_id):
        raise ValueError("重排列表与专题内分组不一致")
    offset = 10000
    for position, group_id in enumerate(ordered_ids):  # 第一段：全体避让
        by_id[group_id].position = offset + position
    session.flush()
    for position, group_id in enumerate(ordered_ids):  # 第二段：落终位
        by_id[group_id].position = position
    session.flush()


def create_topic_with_items(
    db: Session,
    *,
    title: str,
    topic_type: str = "knowledge",
    description: str = "",
    parent_topic_id=None,
    view_mode: str = "linear",
    question_ids=None,
    notes=None,
    group_of=None,
) -> int:
    """创建专题并落初始题目（from-tags / from-paper / duplicate 共用入口）。"""

    title = str(title or "").strip()
    if not title:
        raise ValueError("专题标题不能为空")
    if len(title) > 200:
        raise ValueError("专题标题不能超过 200 字")
    topic_type = validate_topic_type(topic_type)
    view_mode = validate_topic_view_mode(view_mode)
    question_ids = [int(value) for value in (question_ids or [])]
    if len(question_ids) > TOPIC_MAX_ITEMS:
        raise ValueError(f"单个专题题目数上限 {TOPIC_MAX_ITEMS}")
    if len(set(question_ids)) != len(question_ids):
        raise ValueError("同一专题内不能重复添加同一道题")
    if question_ids:
        existing = {
            row[0]
            for row in db.execute(
                text("SELECT id FROM questions WHERE id IN :ids").bindparams(
                    _expanding_ids()
                ),
                {"ids": question_ids},
            )
        }
        missing = sorted(set(question_ids) - existing)
        if missing:
            raise ValueError(f"题目不存在: {missing}")
    if parent_topic_id is not None:
        parent = db.get(Topic, int(parent_topic_id))
        if parent is None:
            raise ValueError("父专题不存在")
    topic = Topic(
        title=title,
        topic_type=topic_type,
        description=description or "",
        parent_topic_id=(int(parent_topic_id) if parent_topic_id is not None else None),
        view_mode=view_mode,
    )
    db.add(topic)
    db.flush()
    for position, question_id in enumerate(question_ids):
        db.add(
            TopicItem(
                topic_id=topic.id,
                question_id=question_id,
                position=position,
                note=(notes or {}).get(question_id, ""),
                group_id=(group_of or {}).get(question_id),
            )
        )
    db.flush()
    return topic.id


def add_items_to_topic(
    db: Session,
    topic: Topic,
    question_ids,
    group_id=None,
    note: str = "",
) -> dict:
    """批量加题：已存在跳过；返回 {added, skipped}（先查后插 + 幂等）。"""

    wanted = []
    seen = set()
    for raw in question_ids or []:
        try:
            question_id = int(raw)
        except (TypeError, ValueError):
            raise ValueError("题目 id 必须为整数")
        if question_id in seen:
            continue
        seen.add(question_id)
        wanted.append(question_id)
    if not wanted:
        return {"added": [], "skipped": []}
    current_count = (
        db.query(TopicItem).filter(TopicItem.topic_id == topic.id).count()
    )
    if current_count + len(wanted) > TOPIC_MAX_ITEMS:
        raise ValueError(
            f"单个专题题目数上限 {TOPIC_MAX_ITEMS}（当前 {current_count}）"
        )
    existing = {
        row[0]
        for row in db.execute(
            text("SELECT id FROM questions WHERE id IN :ids").bindparams(
                _expanding_ids()
            ),
            {"ids": wanted},
        )
    }
    missing = sorted(set(wanted) - existing)
    if missing:
        raise ValueError(f"题目不存在: {missing}")
    already = {
        row[0]
        for row in db.execute(
            text(
                "SELECT question_id FROM topic_items "
                "WHERE topic_id = :t AND question_id IN :ids"
            ).bindparams(_expanding_ids()),
            {"t": topic.id, "ids": wanted},
        )
    }
    if group_id is not None:
        group = db.get(TopicGroup, int(group_id))
        if group is None or group.topic_id != topic.id:
            raise ValueError("分组不存在或不属于该专题")
    next_position = current_count
    added = []
    for question_id in wanted:
        if question_id in already:
            continue
        db.add(
            TopicItem(
                topic_id=topic.id,
                question_id=question_id,
                position=next_position,
                note=note or "",
                group_id=group_id,
            )
        )
        added.append(question_id)
        next_position += 1
    db.flush()
    return {"added": added, "skipped": sorted(already & set(wanted))}


def duplicate_topic(db: Session, topic_id: int, *, title: str = "") -> int:
    """深拷贝 groups+items，自动建 derived_from 链接。"""

    source = db.get(Topic, int(topic_id))
    if source is None:
        raise ValueError("专题不存在")
    groups = (
        db.query(TopicGroup)
        .filter(TopicGroup.topic_id == topic_id)
        .order_by(TopicGroup.position.asc(), TopicGroup.id.asc())
        .all()
    )
    items = (
        db.query(TopicItem)
        .filter(TopicItem.topic_id == topic_id)
        .order_by(TopicItem.position.asc(), TopicItem.id.asc())
        .all()
    )
    new_title = str(title or "").strip() or f"{source.title}（副本）"
    new_id = create_topic_with_items(
        db,
        title=new_title,
        topic_type=source.topic_type,
        description=source.description or "",
        view_mode=source.view_mode or "linear",
        question_ids=[item.question_id for item in items],
        notes={item.question_id: item.note for item in items},
    )
    # 分组深拷贝：保持 items 的相对归属。
    group_map: dict[int, int] = {}
    for group in groups:
        clone = TopicGroup(
            topic_id=new_id,
            title=group.title,
            position=group.position,
            note=group.note,
        )
        db.add(clone)
        db.flush()
        group_map[group.id] = clone.id
    if group_map:
        new_items = (
            db.query(TopicItem)
            .filter(TopicItem.topic_id == new_id)
            .order_by(TopicItem.position.asc(), TopicItem.id.asc())
            .all()
        )
        ordered = sorted(items, key=lambda it: (it.position, it.id))
        for source_item, new_item in zip(ordered, new_items):
            if source_item.group_id in group_map:
                new_item.group_id = group_map[source_item.group_id]
        db.flush()
    link = TopicLink(
        source_topic_id=new_id,
        target_topic_id=int(topic_id),
        link_code="derived_from",
        link_label=TOPIC_LINK_LABELS["derived_from"],
        note="复制派生",
    )
    db.add(link)
    db.flush()
    return new_id


def save_topic_version(db: Session, topic_id: int, note: str = "") -> dict:
    """为专题当前状态追加一个版本快照（append-only）。"""

    topic = db.get(Topic, int(topic_id))
    if topic is None:
        raise ValueError("专题不存在")
    items = (
        db.query(TopicItem)
        .filter(TopicItem.topic_id == topic_id)
        .order_by(TopicItem.position.asc(), TopicItem.id.asc())
        .all()
    )
    snapshot = build_topic_snapshot(topic, items)
    latest = (
        db.query(TopicVersion)
        .filter(TopicVersion.topic_id == topic_id)
        .order_by(TopicVersion.version_no.desc())
        .first()
    )
    version_no = (latest.version_no + 1) if latest else 1
    version = TopicVersion(
        topic_id=topic_id,
        version_no=version_no,
        snapshot_json=json.dumps(snapshot, ensure_ascii=False),
        note=str(note or ""),
        item_count=len(items),
    )
    db.add(version)
    db.flush()
    return version.to_dict()


def restore_topic_from_snapshot(db: Session, topic_id: int, version_no: int) -> dict:
    """回滚 = 追加新版本：先为当前状态存新版，再整体替换为旧快照。"""

    topic = db.get(Topic, int(topic_id))
    if topic is None:
        raise ValueError("专题不存在")
    version = (
        db.query(TopicVersion)
        .filter(TopicVersion.topic_id == topic_id, TopicVersion.version_no == int(version_no))
        .first()
    )
    if version is None:
        raise ValueError("版本不存在")
    try:
        snapshot = json.loads(version.snapshot_json)
    except (TypeError, ValueError) as exc:
        raise ValueError("版本快照损坏，无法恢复") from exc
    if not isinstance(snapshot, dict):
        raise ValueError("版本快照损坏，无法恢复")

    # 先为当前状态存档（恢复动作本身也写「恢复自版本 N」的新版）。
    save_topic_version(db, topic_id, note=f"恢复自版本 {version_no}")

    topic.title = str(snapshot.get("title") or topic.title)
    topic.topic_type = validate_topic_type(snapshot.get("topic_type") or topic.topic_type)
    topic.description = str(snapshot.get("description") or "")
    topic.view_mode = validate_topic_view_mode(snapshot.get("view_mode") or topic.view_mode)

    db.query(TopicItem).filter(TopicItem.topic_id == topic_id).delete(
        synchronize_session=False
    )
    db.query(TopicGroup).filter(TopicGroup.topic_id == topic_id).delete(
        synchronize_session=False
    )
    db.flush()

    group_id_map: dict = {}
    for index, group in enumerate(snapshot.get("groups") or []):
        clone = TopicGroup(
            topic_id=topic_id,
            title=str(group.get("title") or ""),
            position=int(group.get("position", index)),
            note=str(group.get("note") or ""),
        )
        db.add(clone)
        db.flush()
        if group.get("id") is not None:
            group_id_map[group["id"]] = clone.id

    question_ids = [int(item["question_id"]) for item in snapshot.get("items") or []]
    if question_ids:
        existing = {
            row[0]
            for row in db.execute(
                text("SELECT id FROM questions WHERE id IN :ids").bindparams(
                    _expanding_ids()
                ),
                {"ids": list(set(question_ids))},
            )
        }
        missing = sorted(set(question_ids) - existing)
        if missing:
            raise ValueError(f"快照中的题目已被删除，无法恢复: {missing}")
    for index, item in enumerate(snapshot.get("items") or []):
        db.add(
            TopicItem(
                topic_id=topic_id,
                question_id=int(item["question_id"]),
                position=index,
                note=str(item.get("note") or ""),
                group_id=group_id_map.get(item.get("group_id")),
            )
        )
    db.flush()
    return save_topic_version(db, topic_id, note=f"已恢复到版本 {version_no}")


def check_difficulty_balance(db: Session, topic_id: int) -> dict:
    """难度配比计算与失衡提示（规则引擎，全部现有数据可算）。"""

    from mathbank.topic_insights import difficulty_balance_report

    return difficulty_balance_report(db, topic_id)


def topic_children_count(db: Session, topic_id: int) -> int:
    return (
        db.query(Topic)
        .filter(Topic.parent_topic_id == int(topic_id))
        .count()
    )


def collect_descendant_ids(db: Session, topic_id: int) -> list[int]:
    """收集全部后代专题 id（环检测兜底，深度上限 TOPIC_MAX_DEPTH）。"""

    collected: list[int] = []
    frontier = [int(topic_id)]
    depth = 0
    while frontier and depth <= TOPIC_MAX_DEPTH:
        rows = (
            db.query(Topic.id)
            .filter(Topic.parent_topic_id.in_(frontier))
            .all()
        )
        frontier = [row[0] for row in rows]
        collected.extend(frontier)
        depth += 1
    return sorted(set(collected))
