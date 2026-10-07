"""Multi-value question tags and the four-level curriculum tree.

The tag system keeps every multi-valued dimension (mathematical thinking
methods, curriculum nodes, custom labels, functional flags) in one narrow
``question_tags`` table so any dimension can be filtered with an indexed join
instead of scanning JSON blobs.

The curriculum tree upgrades the flat 册→章→节 preset into 册/章/节/小节 with
stable, human readable node codes such as ``B1-C1-S4-P2``.  Codes are derived
from printed textbook numbers only, never from names, so renaming a chapter
never breaks stored data.
"""

from copy import deepcopy
from functools import lru_cache
import json
import re
from typing import Iterable

from mathbank.paths import CURRICULUMS_DIR

RESOURCES_DIR = CURRICULUMS_DIR.parent

TAG_DIMENSIONS = {
    "thought": "数学思想方法",
    "chapter": "教材章节",
    "function": "功能",
    "custom": "自定义标签",
}

# Legacy 册 display names → tree book codes.
LEGACY_BOOK_CODES = {
    "必修一": "B1",
    "必修第一册": "B1",
    "必修二": "B2",
    "必修第二册": "B2",
    "选修一": "X1",
    "选择性必修第一册": "X1",
    "选修二": "X2",
    "选择性必修第二册": "X2",
    "选修三": "X3",
    "选择性必修第三册": "X3",
}

_LEADING_NUMBER = re.compile(r"(\d+)")


def _first_number(text: str):
    match = _LEADING_NUMBER.search(str(text or ""))
    return int(match.group(1)) if match else None


@lru_cache(maxsize=4)
def _load_tree_cached(version: str) -> dict:
    code = str(version or "A").strip().upper()
    path = CURRICULUMS_DIR / f"{code}2019.json"
    if not path.exists():
        raise ValueError(f"未找到该教材版本的四级章节树: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or "books" not in data:
        raise ValueError(f"四级章节树格式错误: {path}")
    return data


def load_curriculum_tree(version: str = "A") -> dict:
    """Return an isolated copy of the four-level curriculum tree."""

    return deepcopy(_load_tree_cached(str(version or "A").strip().upper()))


@lru_cache(maxsize=4)
def _tree_index_cached(version: str) -> dict:
    """Build a flat index of every node: code → {path, level, ...}."""

    index: dict[str, dict] = {}
    for book in _load_tree_cached(version).get("books", []):
        index[book["code"]] = {
            "code": book["code"],
            "level": "book",
            "name": book.get("name", ""),
            "path": book.get("name", ""),
            "book": book["code"],
        }
        for chapter in book.get("chapters", []):
            index[chapter["code"]] = {
                "code": chapter["code"],
                "level": "chapter",
                "name": chapter.get("name", ""),
                "path": chapter.get("path", ""),
                "book": book["code"],
                "chapter_no": chapter.get("no"),
            }
            for section in chapter.get("sections", []):
                index[section["code"]] = {
                    "code": section["code"],
                    "level": "section",
                    "name": section.get("name", ""),
                    "path": section.get("path", ""),
                    "book": book["code"],
                    "chapter_no": chapter.get("no"),
                    "section_no": section.get("no"),
                    "optional": bool(section.get("optional")),
                }
                for sub in section.get("subsections", []):
                    index[sub["code"]] = {
                        "code": sub["code"],
                        "level": "subsection",
                        "name": sub.get("name", ""),
                        "path": sub.get("path", ""),
                        "book": book["code"],
                        "chapter_no": chapter.get("no"),
                        "section_no": section.get("no"),
                        "subsection_no": sub.get("no"),
                    }
    return index


def curriculum_index(version: str = "A") -> dict:
    """Return a read-only mapping of node code → node metadata."""

    return _tree_index_cached(str(version or "A").strip().upper())


def node_path(code: str, version: str = "A") -> str:
    """Return the full Chinese path of one node code ('' when unknown)."""

    node = curriculum_index(version).get(str(code or "").strip())
    return node["path"] if node else ""


def is_valid_node_code(code: str, version: str = "A") -> bool:
    return str(code or "").strip() in curriculum_index(version)


def resolve_legacy_code(
    compulsory: str,
    chapter: str,
    knowledge: str = "",
    version: str = "A",
) -> str:
    """Translate a legacy 册/章/节 triple into a node code.

    Returns the deepest matching node (小节 > 节 > 章 > 册) or '' when the
    triple cannot be located.  Legacy rows are preserved untouched; this only
    seeds the new multi-value tag table.
    """

    book_code = LEGACY_BOOK_CODES.get(str(compulsory or "").strip())
    if not book_code:
        return ""
    chapter_no = _first_number(chapter)
    if chapter_no is None:
        return ""
    chapter_code = f"{book_code}-C{chapter_no}"
    index = curriculum_index(version)
    if chapter_code not in index:
        return book_code

    # knowledge looks like "1.4 充分条件与必要条件" or "1.4.2 充要条件".
    match = re.match(r"\s*(\d+)\.(\d+)(?:\.(\d+))?", str(knowledge or ""))
    if not match:
        return chapter_code
    if int(match.group(1)) != chapter_no:
        return chapter_code
    section_code = f"{chapter_code}-S{int(match.group(2))}"
    if match.group(3):
        sub_code = f"{section_code}-P{int(match.group(3))}"
        if sub_code in index:
            return sub_code
    return section_code if section_code in index else chapter_code


def chapter_prefixes(code: str) -> list[str]:
    """Return LIKE patterns matching the node and everything below it.

    Attaching a parent node is treated as covering all of its children, so a
    question tagged ``B1-C5`` is also a match for the 5.3 诱导公式 query.
    """

    code = str(code or "").strip()
    return [code, f"{code}-%"] if code else []


def normalize_codes(values: Iterable, version: str = "A") -> list[str]:
    """Keep only known node codes, preserving order and removing duplicates."""

    index = curriculum_index(version)
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        code = str(value or "").strip()
        if code and code in index and code not in seen:
            seen.add(code)
            result.append(code)
    return result


@lru_cache(maxsize=1)
def _load_tag_schema_cached() -> dict:
    path = RESOURCES_DIR / "tag_schema.json"
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or "dimensions" not in data:
        raise ValueError(f"标签体系定义格式错误: {path}")
    return data


def load_tag_schema() -> dict:
    """Return an isolated copy of the dimension definitions."""

    return deepcopy(_load_tag_schema_cached())


def _dimension_storage_key(dimension: dict) -> str:
    """Parse the ``question_tags(dim='...')`` short name from a dimension's
    ``storage`` field, returning '' when the dimension is not tag-backed."""

    storage = str(dimension.get("storage") or "")
    match = re.search(r"dim=['\"]?([a-z_]+)['\"]?", storage)
    return match.group(1) if match else ""


def allowed_codes(dim: str) -> set[str]:
    """Return the controlled vocabulary of one dimension.

    ``dim`` may be either the schema ``key`` (``thought_method``) or the
    storage short name (``thought``) so callers can validate by the narrow
    ``question_tags.dim`` value without duplicating the mapping.
    """

    for dimension in _load_tag_schema_cached().get("dimensions", []):
        if dimension.get("key") == dim or _dimension_storage_key(dimension) == dim:
            return {item["code"] for item in dimension.get("values", []) if item.get("code")}
    return set()


def normalize_tag_codes(dim: str, values: Iterable) -> list[str]:
    """Validate and de-duplicate tag codes for one dimension."""

    allowed = allowed_codes(dim)
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        code = str(value or "").strip()
        if not code or code in seen:
            continue
        seen.add(code)
        if dim == "chapter":
            # Chapter codes are validated against the tree, not the schema file.
            continue
        if allowed and code not in allowed:
            continue
        result.append(code)
    return result


def split_custom_tags(text: str) -> list[str]:
    """Split a comma / Chinese-comma separated custom tag string."""

    parts = re.split(r"[,，;；\n]+", str(text or ""))
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        tag = part.strip()
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result
