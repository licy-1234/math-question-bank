"""专题推荐器（规则引擎）：同模型 / 同解法 / 变式推荐。

全部基于现有数据（question_tags / question_relations / questions），
确定、可解释、离线，不依赖 LLM。每条推荐必带 reasons。
"""

from __future__ import annotations

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from mathbank.topics import (
    DIFFICULTY_PROFILES,
    LEVEL_LABEL,
)

#: 关系是教师/AI 人工标注的 ground truth，直接关系主导打分。
RELATION_WEIGHTS = {
    "one_q_variants": 1.00,
    "multi_q_one_solution": 0.90,
    "multi_q_unify": 0.85,
    "same_topic": 0.60,
    "one_method_multi_use": 0.55,
    "multi_method_one": 0.50,
    "legacy_group": 0.40,
}
RELATION_LABELS = {
    "one_q_variants": "一题多变",
    "multi_q_one_solution": "多题一解",
    "multi_q_unify": "多题归一",
    "same_topic": "同题",
    "one_method_multi_use": "一法多用",
    "multi_method_one": "多法一用",
    "legacy_group": "题组",
}

#: 维度加权 Jaccard 的权重（各类型和为 1）。
TAG_WEIGHTS = {
    "method": {"strategy": .35, "micro": .30, "thought": .15, "function": .10, "chapter": .10},
    "knowledge": {"chapter": .35, "micro": .20, "strategy": .15, "thought": .10, "function": .20},
}
DIM_LABELS = {
    "thought": "宏观思想",
    "strategy": "中观策略",
    "micro": "微观技法",
    "function": "功能",
    "chapter": "章节",
    "custom": "自定义标签",
}

RECOMMEND_THRESHOLD = 0.45
TWO_HOP_DECAY = 0.5
#: micro 的 L2 家族（代码「L2名·L3名」的前缀）相同记 0.5 分。
MICRO_FAMILY_MATCH = 0.5
MICRO_FAMILY_SEPARATOR = "·"
#: context_score 封顶 0.10。
CONTEXT_CAP = 0.10
CONTEXT_QUESTION_TYPE = 0.04
CONTEXT_DIFFICULTY = 0.03
CONTEXT_HAS_ANSWER = 0.02
CONTEXT_USAGE = 0.01


def _expanding(name: str):
    return bindparam(name, expanding=True)


def _tag_map(db: Session, question_ids) -> dict[int, dict[str, set[str]]]:
    """dim -> codes per question（一次查询 hydrate，禁 N+1）。"""

    ids = sorted({int(value) for value in question_ids})
    if not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT question_id, dim, code FROM question_tags "
            "WHERE question_id IN :ids"
        ).bindparams(_expanding("ids")),
        {"ids": ids},
    ).fetchall()
    mapping: dict[int, dict[str, set[str]]] = {}
    for question_id, dim, code in rows:
        mapping.setdefault(int(question_id), {}).setdefault(str(dim), set()).add(str(code))
    return mapping


def _micro_family(code: str) -> str:
    return code.split(MICRO_FAMILY_SEPARATOR, 1)[0]


def _soft_jaccard(candidate_codes: set[str], seed_codes: set[str], *, micro: bool = False) -> float:
    """加权 Jaccard；micro 维度下 L2 家族相同记 0.5。"""

    if not candidate_codes or not seed_codes:
        return 0.0
    if not micro:
        intersection = len(candidate_codes & seed_codes)
        union = len(candidate_codes | seed_codes)
        return intersection / union if union else 0.0
    intersection = 0.0
    for candidate_code in candidate_codes:
        if candidate_code in seed_codes:
            intersection += 1.0
            continue
        candidate_family = _micro_family(candidate_code)
        if any(_micro_family(seed_code) == candidate_family for seed_code in seed_codes):
            intersection += MICRO_FAMILY_MATCH
    union = len(candidate_codes) + len(seed_codes) - intersection
    return intersection / union if union > 0 else 0.0


def _relation_neighborhood(db: Session, seed_ids) -> tuple[dict[int, float], set[int]]:
    """返回 (候选 -> 与种子的最强关系分, 种子邻居集合)。

    二跳（候选与种子邻居的关系）按 TWO_HOP_DECAY 衰减。
    """

    ids = sorted({int(value) for value in seed_ids})
    rows = db.execute(
        text(
            "SELECT question_id, related_id, relation_code FROM question_relations "
            "WHERE question_id IN :ids OR related_id IN :ids"
        ).bindparams(_expanding("ids")),
        {"ids": ids},
    ).fetchall()
    direct: dict[int, float] = {}
    neighbors: set[int] = set()
    for question_id, related_id, relation_code in rows:
        left, right = int(question_id), int(related_id)
        other = right if left in set(ids) else left
        neighbors.add(other)
        weight = RELATION_WEIGHTS.get(str(relation_code), 0.0)
        if weight > direct.get(other, 0.0):
            direct[other] = weight
    neighbors -= set(ids)
    return direct, neighbors


def _hop2_scores(db: Session, candidate_ids, neighbors: set[int]) -> dict[int, float]:
    """候选与种子邻居的关系分（二跳，衰减后）。"""

    ids = sorted({int(value) for value in candidate_ids})
    if not ids or not neighbors:
        return {}
    rows = db.execute(
        text(
            "SELECT question_id, related_id, relation_code FROM question_relations "
            "WHERE (question_id IN :ids AND related_id IN :neighbors) "
            "OR (related_id IN :ids AND question_id IN :neighbors)"
        ).bindparams(_expanding("ids"), _expanding("neighbors")),
        {"ids": ids, "neighbors": sorted(neighbors)},
    ).fetchall()
    scores: dict[int, float] = {}
    for question_id, related_id, relation_code in rows:
        candidate = int(question_id)
        if candidate not in set(ids):
            candidate = int(related_id)
        weight = RELATION_WEIGHTS.get(str(relation_code), 0.0) * TWO_HOP_DECAY
        if weight > scores.get(candidate, 0.0):
            scores[candidate] = weight
    return scores


def _related(db: Session, left: int, right: int) -> bool:
    row = db.execute(
        text(
            "SELECT 1 FROM question_relations "
            "WHERE (question_id = :a AND related_id = :b) "
            "OR (question_id = :b AND related_id = :a) LIMIT 1"
        ),
        {"a": int(left), "b": int(right)},
    ).first()
    return row is not None


def _question_rows(db: Session, question_ids) -> dict[int, tuple]:
    """一次查询 hydrate 候选题目行（禁 N+1）。"""

    ids = sorted({int(value) for value in question_ids})
    if not ids:
        return {}
    rows = db.execute(
        text(
            "SELECT id, question_type, difficulty, answer_markdown, usage_count "
            "FROM questions WHERE id IN :ids"
        ).bindparams(_expanding("ids")),
        {"ids": ids},
    ).fetchall()
    return {
        int(row[0]): (row[1], row[2], row[3], row[4])
        for row in rows
    }


def _score_one(
    db: Session,
    candidate_id: int,
    *,
    seed_ids: list[int],
    seed_seq: dict[int, int],
    seed_tags: dict[int, dict[str, set[str]]],
    seed_types: dict[str, int],
    candidate_tags: dict[str, set[str]],
    candidate_row: tuple | None,
    relation_score: float,
    relation_reason: str,
    topic_type: str,
    target_profile: dict[str, float],
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    if relation_reason:
        reasons.append(relation_reason)

    tag_weights = TAG_WEIGHTS.get(topic_type) or TAG_WEIGHTS["knowledge"]
    seed_union: dict[str, set[str]] = {}
    for tags in seed_tags.values():
        for dim, codes in tags.items():
            seed_union.setdefault(dim, set()).update(codes)
    tag_score = 0.0
    for dim, weight in tag_weights.items():
        score = _soft_jaccard(
            candidate_tags.get(dim, set()),
            seed_union.get(dim, set()),
            micro=(dim == "micro"),
        )
        tag_score += weight * score
        if score >= 0.5 and candidate_tags.get(dim):
            shared = sorted(candidate_tags[dim] & seed_union.get(dim, set()))
            if shared:
                reasons.append(f"共用{DIM_LABELS.get(dim, dim)}：{'、'.join(shared[:3])}")
            elif dim == "micro":
                families = sorted(
                    {
                        _micro_family(code)
                        for code in candidate_tags[dim]
                        if any(
                            _micro_family(seed_code) == _micro_family(code)
                            for seed_code in seed_union.get(dim, set())
                        )
                    }
                )
                if families:
                    reasons.append(f"同属{DIM_LABELS[dim]}家族：{'、'.join(families[:3])}")

    context_score = 0.0
    if candidate_row is not None:
        question_type, difficulty, answer, usage_count = candidate_row
        majority_type = max(seed_types, key=seed_types.get) if seed_types else None
        if majority_type and question_type == majority_type:
            context_score += CONTEXT_QUESTION_TYPE
            reasons.append("题型与专题主体一致")
        level = str(difficulty or "").strip().lower()
        if level in target_profile:
            ranked = sorted(target_profile, key=lambda item: -target_profile[item])
            if level in ranked[:2]:
                context_score += CONTEXT_DIFFICULTY
                reasons.append(f"难度（{LEVEL_LABEL.get(level, level)}）符合目标配比")
        if str(answer or "").strip():
            context_score += CONTEXT_HAS_ANSWER
            reasons.append("已有答案解析")
        if int(usage_count or 0) >= 3:
            context_score += CONTEXT_USAGE
            reasons.append("已被多次使用，质量经过验证")
    context_score = min(context_score, CONTEXT_CAP)

    total = 0.55 * relation_score + 0.35 * tag_score + 0.10 * context_score
    if not reasons:
        reasons.append("与专题内题目在标签或关系上相近")
    return total, reasons


def recommend_questions(
    db: Session,
    topic_id: int,
    *,
    limit: int = 5,
    diversity: bool = True,
) -> list[dict]:
    """规则引擎推荐：同模型 / 同解法 / 变式，Top N 且每条必带 reasons。"""

    seeds = db.execute(
        text(
            "SELECT ti.question_id, ti.position FROM topic_items ti "
            "WHERE ti.topic_id = :t ORDER BY ti.position ASC"
        ),
        {"t": int(topic_id)},
    ).fetchall()
    if not seeds:
        return []
    seed_ids = [int(row[0]) for row in seeds]
    seed_seq = {int(row[0]): int(row[1]) + 1 for row in seeds}

    seed_tags = _tag_map(db, seed_ids)
    seed_codes: set[str] = set()
    for tags in seed_tags.values():
        for dim in ("strategy", "micro"):
            seed_codes.update(tags.get(dim, set()))

    # 候选预过滤：全命中现有索引（idx_question_tags_dim_code / idx_qrel_* 双向）。
    candidates: set[int] = set()
    if seed_codes:
        for row in db.execute(
            text(
                "SELECT DISTINCT question_id FROM question_tags "
                "WHERE dim IN ('strategy','micro') AND code IN :codes"
            ).bindparams(_expanding("codes")),
            {"codes": sorted(seed_codes)},
        ):
            candidates.add(int(row[0]))
    direct, neighbors = _relation_neighborhood(db, seed_ids)
    candidates.update(direct.keys())
    candidates -= set(seed_ids)
    if not candidates:
        return []

    hop2 = _hop2_scores(db, candidates, neighbors)

    # 关系理由：找到与哪个种子、哪种关系。
    relation_rows = db.execute(
        text(
            "SELECT question_id, related_id, relation_code FROM question_relations "
            "WHERE (question_id IN :ids OR related_id IN :ids) "
            "AND (question_id IN :candidates OR related_id IN :candidates)"
        ).bindparams(_expanding("ids"), _expanding("candidates")),
        {"ids": seed_ids, "candidates": sorted(candidates)},
    ).fetchall()
    best_relation: dict[int, tuple[float, str]] = {}
    for question_id, related_id, relation_code in relation_rows:
        left, right = int(question_id), int(related_id)
        if left in set(seed_ids) and right in set(seed_ids):
            continue
        if left in set(seed_ids):
            candidate, seed = right, left
        else:
            candidate, seed = left, right
        weight = RELATION_WEIGHTS.get(str(relation_code), 0.0)
        if weight > best_relation.get(candidate, (0.0, ""))[0]:
            label = RELATION_LABELS.get(str(relation_code), str(relation_code))
            best_relation[candidate] = (
                weight,
                f"与第 {seed_seq.get(seed, '?')} 题同属『{label}』",
            )

    type_rows = db.execute(
        text(
            "SELECT question_type, COUNT(*) AS n FROM questions "
            "WHERE id IN :ids GROUP BY question_type"
        ).bindparams(_expanding("ids")),
        {"ids": seed_ids},
    ).fetchall()
    seed_types = {str(row[0] or ""): int(row[1]) for row in type_rows}

    topic = db.execute(
        text("SELECT topic_type FROM topics WHERE id = :t"), {"t": int(topic_id)}
    ).first()
    topic_type = str(topic[0]) if topic else "knowledge"
    target_profile = DIFFICULTY_PROFILES.get(topic_type) or DIFFICULTY_PROFILES["knowledge"]

    scored: list[tuple[float, int, list[str]]] = []
    candidate_tags_map = _tag_map(db, candidates)
    candidate_rows = _question_rows(db, candidates)
    for candidate_id in candidates:
        relation_score = max(
            best_relation.get(candidate_id, (0.0, ""))[0],
            direct.get(candidate_id, 0.0),
            hop2.get(candidate_id, 0.0),
        )
        relation_reason = best_relation.get(candidate_id, (0.0, ""))[1]
        score, reasons = _score_one(
            db,
            candidate_id,
            seed_ids=seed_ids,
            seed_seq=seed_seq,
            seed_tags=seed_tags,
            seed_types=seed_types,
            candidate_tags=candidate_tags_map.get(candidate_id, {}),
            candidate_row=candidate_rows.get(candidate_id),
            relation_score=relation_score,
            relation_reason=relation_reason,
            topic_type=topic_type,
            target_profile=target_profile,
        )
        if score >= RECOMMEND_THRESHOLD:
            scored.append((score, candidate_id, reasons))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))

    if not diversity:
        return [
            {"question_id": qid, "score": round(score, 3), "reasons": reasons}
            for score, qid, reasons in scored[:limit]
        ]
    # 已选推荐之间互为变式/同模型的跳过，防专题同质化。
    picked: list[tuple[float, int, list[str]]] = []
    for entry in scored:
        if any(_related(db, entry[1], other[1]) for other in picked):
            continue
        picked.append(entry)
        if len(picked) >= limit:
            break
    return [
        {"question_id": qid, "score": round(score, 3), "reasons": reasons}
        for score, qid, reasons in picked
    ]
