"""Curriculum preset loading and metadata defaults.

The textbook trees live in JSON resources so the backend and browser use
one authoritative copy instead of maintaining independent Python and
JavaScript constants.
"""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
import json
import re

from mathbank.paths import CURRICULUMS_DIR


#: 本地定案：教材只保留人教A版（2026-10-20 用户明确要求）。
#: 上游 V3.0.0 新增的 N（北师大）/X（湘教）不注册；B/S/H 一并下线，
#: 因为题库 28 道题的 question_curriculums.version_code 全部为 A（已核验），
#: 不保留任何非 A 的加载路径，避免"能载入但不是本地定案"的歧义。
CURRICULUM_NAMES = {
    "A": "人教A版",
}

DEFAULT_QUESTION_TYPES = [
    {"value": "single_choice", "label": "单选题"},
    {"value": "multi_choice", "label": "多选题"},
    {"value": "fill_in_blank", "label": "填空题"},
    {"value": "detailed_answer", "label": "解答题"},
]

DEFAULT_DIFFICULTIES = [
    {
        "value": "easy",
        "label": "基础题",
        "color": "text-green-600 bg-green-50 border-green-200",
    },
    {
        "value": "medium",
        "label": "中档题",
        "color": "text-blue-600 bg-blue-50 border-blue-200",
    },
    {
        "value": "hard",
        "label": "难题",
        "color": "text-red-600 bg-red-50 border-red-200",
    },
]

#: 难度收敛为本地三级（easy / medium / hard）。上游 V3.0.0 及历史数据用的是
#: 四级值 normal / easy_error / challenge / qiangji，读取侧（AI 元数据、PDF
#: 结构解析、分享包导入）仍可能遇到，逐条映射到三级后再校验与落库；写库口径
#: 与统计口径始终只有三级。映射与 tools/deploy/migrate_difficulty.py 的迁移表
#: 逐条一致（已由用户确认）。
LEGACY_DIFFICULTIES = {
    "normal": "medium",
    "easy_error": "medium",
    "challenge": "hard",
    "qiangji": "hard",
}

DIFFICULTY_VALUES = frozenset(row["value"] for row in DEFAULT_DIFFICULTIES)


def normalize_difficulty(value: str) -> str:
    """Map a legacy four-level difficulty onto the local three-level standard."""

    text = str(value or "").strip().lower()
    return LEGACY_DIFFICULTIES.get(text, text)


def normalize_version_code(version: str) -> str:
    """Validate and normalize a curriculum version code."""

    code = str(version or "A").strip().upper()
    if code not in CURRICULUM_NAMES:
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


#: 知识点目录资源（mathbank/resources/knowledge_points.json）是按多教材编排的
#: 权威快照：每条知识点带 curriculum_paths / curriculum_hierarchies，用
#: version_code 指明它属于哪一版教材。教材收敛为人教A版后，这些**资源内部的
#: 归属校验**仍必须能读到 B/S/H/N/X 的教材树，否则整份资源无法装载、启动即崩；
#: 但产品层（配置、预设、下拉、目录接口）依旧只放行 A。因此下面这组函数只供
#: 资源校验使用，不得用于对外暴露版本。
RESOURCE_CURRICULUM_CODES = ("A", "B", "S", "H", "N", "X")


def normalize_resource_version_code(version: str) -> str:
    """Validate a curriculum code against the on-disk resource set."""

    code = str(version or "A").strip().upper()
    if code not in RESOURCE_CURRICULUM_CODES or not (CURRICULUMS_DIR / f"{code}.json").is_file():
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


@lru_cache(maxsize=len(RESOURCE_CURRICULUM_CODES))
def _load_resource_curriculum_cached(version: str) -> dict:
    code = normalize_resource_version_code(version)
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"教材大纲资源格式错误: {path}")
    return data


def load_resource_curriculum(version: str = "A") -> dict:
    """Return an isolated copy of a curriculum tree bypassing the product gate."""

    return deepcopy(_load_resource_curriculum_cached(normalize_resource_version_code(version)))


@lru_cache(maxsize=len(CURRICULUM_NAMES))
def _load_curriculum_cached(version: str) -> dict:
    code = normalize_version_code(version)
    path = CURRICULUMS_DIR / f"{code}.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"教材大纲资源格式错误: {path}")
    return data


def load_curriculum(version: str = "A") -> dict:
    """Return an isolated copy of one curriculum tree."""

    return deepcopy(_load_curriculum_cached(normalize_version_code(version)))


def build_default_metadata(version: str = "A") -> dict:
    """Build the editable metadata payload used by settings and first boot."""

    code = normalize_version_code(version)
    return {
        "curriculum_version": code,
        "question_types": deepcopy(DEFAULT_QUESTION_TYPES),
        "difficulties": deepcopy(DEFAULT_DIFFICULTIES),
        "curriculum": load_curriculum(code),
    }


def get_curriculum_preset(version: str = "A") -> dict:
    """Return the API representation of a curriculum preset."""

    code = normalize_version_code(version)
    return {
        "version": code,
        "name": CURRICULUM_NAMES[code],
        "metadata": build_default_metadata(code),
    }


# A catalog label carries a decorative front code ("2. 直线和圆的方程",
# "2.5 直线与圆、圆与圆的位置关系", "10. 概率", "第一章 集合与常用逻辑用语").
# A book label carries an equivalent alias ("选修一" == "选择性必修第一册").
# Models reliably copy the human name but frequently drop/normalize that code,
# and the historical rows store the alias form.  The code/alias is presentation
# only: the identity is the trailing name, so resolving a name back to its
# unique catalog label keeps the strict "only catalog pairs" contract usable
# instead of silently rejecting a whole question group.
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
_FRONT_CODE = re.compile(
    r"^\s*(?:第\s*[0-9一二三四五六七八九十]+\s*[章节讲单元部分]"
    r"|\d+(?:\.\d+)*\s*[.．、]?)\s*"
)
_BOOK = re.compile(r"(选择性)?\s*(必修|选修)\s*第?\s*([0-9一二三四五六七八九十]+)\s*册?")


def _plain(value) -> str:
    """Normalize width/space so catalog comparisons ignore cosmetic differences."""

    return str(value or "").translate(_FULLWIDTH_DIGITS).replace("\u3000", " ").strip()


def _small_number(token: str) -> int | None:
    token = _plain(token)
    if token.isdigit():
        return int(token)
    return _CN_DIGITS.get(token)


def _book_signature(label: str) -> tuple[str, int] | None:
    """Return ('必修'|'选修', N) for a book label, folding 选择性必修 into 选修."""

    match = _BOOK.search(_plain(label))
    if not match:
        return None
    kind = "选修" if (match.group(1) or match.group(2) == "选修") else "必修"
    number = _small_number(match.group(3))
    return (kind, number) if number is not None else None


def curriculum_display_label(label: str) -> str:
    """Return the chapter/section name with its leading catalog code removed."""

    return _FRONT_CODE.sub("", _plain(label), count=1).strip()


def _resolve_catalog_label(
    options: Iterable[str], value, *, allow_book_signature: bool = False,
) -> str | None:
    """Resolve one label to an exact catalog key, or ``None`` when ambiguous."""

    text = _plain(value)
    if not text:
        return None
    options = [option for option in options if isinstance(option, str)]
    for option in options:
        if _plain(option) == text:
            return option
    display = curriculum_display_label(text)
    if display:
        matches = [option for option in options if curriculum_display_label(option) == display]
        if len(matches) == 1:
            return matches[0]
    if allow_book_signature:
        signature = _book_signature(text)
        if signature is not None:
            matches = [option for option in options if _book_signature(option) == signature]
            if len(matches) == 1:
                return matches[0]
    return None


def canonical_category_labels(
    curriculum: Mapping, compulsory, chapter, knowledge="",
) -> tuple[str, str, str] | None:
    """Resolve model-supplied 学段/章节/小节 to the exact active-catalog keys.

    Resolution tolerates the cosmetic forms a model or an older row may use:
    a dropped front code ("直线和圆的方程" -> "2. 直线和圆的方程"), a "第2章"
    wrapper, full-width digits/spaces, and the book alias
    ("选择性必修第一册" -> "选修一").  Returns ``None`` only when the 册 or 章
    cannot be resolved uniquely, so callers keep their strict rejection.  An
    unresolvable 小节 is *downgraded* to the whole chapter (empty string), which
    is the documented "整章待细分" state — never a group-wide failure.
    """

    if not isinstance(curriculum, Mapping) or not curriculum:
        return None
    resolved_compulsory = _resolve_catalog_label(
        curriculum.keys(), compulsory, allow_book_signature=True)
    if resolved_compulsory is None:
        return None
    chapters = curriculum.get(resolved_compulsory)
    if not isinstance(chapters, Mapping):
        return None
    resolved_chapter = _resolve_catalog_label(chapters.keys(), chapter)
    if resolved_chapter is None:
        return None
    sections = chapters.get(resolved_chapter) or ()
    if not _plain(knowledge):
        return resolved_compulsory, resolved_chapter, ""
    resolved_knowledge = _resolve_catalog_label(sections, knowledge)
    if resolved_knowledge is None:
        # A fabricated 小节 must not discard the whole question: keep the
        # chapter and fall back to the legal "整章待细分" state.
        return resolved_compulsory, resolved_chapter, ""
    return resolved_compulsory, resolved_chapter, resolved_knowledge
def build_curriculum_routing_index(paths, source_version, target_version, target_curriculum, *, target_ancestors=None):
    """Index verified shared point paths once for a user-requested switch.

    This is a directory crosswalk, not a question classifier.  Only exact
    source paths and real target paths participate; no topic-name inference
    or chapter-number comparison is performed.  ``target_ancestors`` contains
    source-verified ancestry for this target version only, indexed by
    (book, chapter, leaf reference section); absent evidence stays ambiguous.
    """
    order = {(book, chapter): position
             for position, (book, chapter) in enumerate(
                 (book, chapter) for book, chapters in target_curriculum.items()
                 if isinstance(chapters, dict) for chapter in chapters)}
    ancestor_titles = {}
    for (book, chapter, _reference), lineage in (target_ancestors or {}).items():
        ancestor_titles.setdefault((book, chapter), set()).update("".join(title.split()) for title in lineage)
    parent_sections = {(book, chapter, section)
                       for (book, chapter) in order for section in target_curriculum[book][chapter]
                       if "".join(section.split()) in ancestor_titles.get((book, chapter), set())}
    target_by_code, source_by_code, target_records = {}, {}, {}
    for path in paths:
        code = path.get("point_code")
        if not code:
            continue
        location = tuple(path.get(field, "") or "" for field in ("compulsory", "chapter", "knowledge"))
        if path.get("version_code") == target_version:
            book, chapter, section = location
            if ((book, chapter) not in order
                    or section and section not in target_curriculum[book][chapter]):
                continue
            target_records.setdefault(code, []).append({**path, "location": location})
        if path.get("version_code") == source_version:
            source_by_code.setdefault(code, set()).add(location)
    for code, records in target_records.items():
        leaves = [record for record in records if record.get("level", "leaf") == "leaf"
                  and record["location"][2] and record["location"] not in parent_sections]
        for record in records:
            location = record["location"]
            if record.get("level", "leaf") == "leaf" and location[2] and location not in parent_sections:
                target_by_code.setdefault(code, set()).add(location)
                continue
            # Parent/whole-chapter recommendations do not establish a terminal
            # section.  Remove one only when an actual leaf proves it redundant.
            redundant = any(_is_proven_path_ancestor(record, leaf, target_ancestors or {}) for leaf in leaves)
            if not redundant:
                target_by_code.setdefault(code, set()).add((*location[:2], ""))
    by_source = {}
    for code, source_paths in source_by_code.items():
        targets = target_by_code.get(code, set())
        for book, chapter, section in source_paths:
            by_source.setdefault((book, chapter, section), set()).update(targets)
            by_source.setdefault((book, chapter, ""), set()).update(targets)
    return {"by_code": target_by_code, "by_source": by_source,
            "order": order, "curriculum": target_curriculum, "parent_sections": parent_sections}


def _is_proven_path_ancestor(parent, leaf, ancestors):
    if parent["location"][:2] != leaf["location"][:2]:
        return False
    # Some presets intentionally expose one local section for several source
    # sub-sections.  Equal validated local paths already prove equivalence at
    # the precision this preset can store; no number-based inference is needed.
    if parent["location"] == leaf["location"]:
        return True
    if not parent["location"][2] or parent.get("level") == "chapter":
        return True
    book, chapter, section = leaf["location"]
    reference = leaf.get("reference_section", section)
    lineage = ancestors.get((book, chapter, reference), leaf.get("ancestors", ()))
    return parent.get("reference_section", parent["location"][2]) in lineage


def _single_chapter_location(locations):
    chapters = {(book, chapter) for book, chapter, _ in locations}
    if len(chapters) != 1:
        return None
    book, chapter = next(iter(chapters))
    sections = {section for _, _, section in locations}
    return book, chapter, next(iter(sections)) if len(sections) == 1 else ""


def route_curriculum_location(compulsory, chapter, knowledge, routing_index, *, point_codes=()):
    """Use confirmed main points, then an unambiguous directory crosswalk.

    Different points that each identify a chapter form a combined question;
    the later chapter follows actual target-directory order.  One point with
    alternative uses across chapters is ambiguous and cannot prove that all
    of those chapters were used by this question.
    """
    by_code, by_source = routing_index["by_code"], routing_index["by_source"]
    point_locations = [by_code[code] for code in dict.fromkeys(point_codes) if by_code.get(code)]
    reliable = [location for locations in point_locations
                if (location := _single_chapter_location(locations)) is not None]
    if reliable:
        target_chapter = max((location[:2] for location in reliable), key=routing_index["order"].__getitem__)
        # An ambiguous main point might require a later chapter than the
        # already-proven parts.  Do not silently discard that uncertainty.
        if any(_single_chapter_location(locations) is None
               and any(routing_index["order"][location[:2]] > routing_index["order"][target_chapter]
                       for location in locations) for locations in point_locations):
            return "", "", ""
        candidates = {location for locations in point_locations for location in locations
                      if location[:2] == target_chapter}
        return _single_chapter_location(candidates)

    source_location = (compulsory or "", chapter or "", knowledge or "")
    has_exact_section = bool(source_location[2]) and source_location in by_source
    chapter_fallback = not has_exact_section
    candidates = (by_source[source_location] if has_exact_section
                  else by_source.get((*source_location[:2], ""), set()))
    if has_exact_section and not candidates:
        # This source section is known but has no verified target mapping.
        # A neighbouring section must not stand in for its missing evidence.
        return "", "", ""
    source_match = _single_chapter_location(candidates)
    if source_match is not None:
        # Source context can disambiguate a multi-purpose point, but cannot
        # select a target chapter absent from that point's actual paths.
        if point_locations:
            contextual = {location for locations in point_locations for location in locations
                          if location[:2] == source_match[:2]}
            if contextual:
                return _single_chapter_location(contextual)
            return "", "", ""
        # An entire source chapter does not establish that this particular
        # question belongs to the one target section represented in the data.
        return (*source_match[:2], "") if chapter_fallback else source_match
    if point_locations:
        return "", "", ""

    # Preserve a complete literal path when directories share one.  Matching
    # only a generic section label is not sufficient evidence.
    tree = routing_index["curriculum"]
    book, title, section = source_location
    def terminal_or_empty(book, chapter, section):
        return section if section in tree[book][chapter] and (book, chapter, section) not in routing_index["parent_sections"] else ""
    if book in tree and title in tree[book]:
        return book, title, terminal_or_empty(book, title, section)
    exact_chapters = [(book, title, terminal_or_empty(book, title, section))
                      for book, chapters in tree.items() if isinstance(chapters, dict)
                      for name in chapters if name == title]
    return exact_chapters[0] if len(exact_chapters) == 1 else ("", "", "")
