"""Curriculum preset loading and metadata defaults.

The four textbook trees live in JSON resources so the backend and browser use
one authoritative copy instead of maintaining independent Python and
JavaScript constants.
"""

from collections.abc import Iterable, Mapping
from copy import deepcopy
from functools import lru_cache
import json
import re

from mathbank.paths import CURRICULUMS_DIR


CURRICULUM_NAMES = {
    "A": "人教A版",
    "B": "人教B版",
    "S": "苏教版",
    "H": "沪教版",
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


def normalize_version_code(version: str) -> str:
    """Validate and normalize a curriculum version code."""

    code = str(version or "A").strip().upper()
    if code not in CURRICULUM_NAMES:
        raise ValueError(f"不支持的教材大纲版本: {version}")
    return code


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
