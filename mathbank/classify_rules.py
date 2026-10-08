"""Evidence-based question classification decision engine.

This module is the single source of truth for question type / difficulty /
chapter decisions.  It is intentionally dependency free (standard library
only) so that ``main.py`` (FastAPI runtime) and ``tools/classify_eval/``
(offline evaluation) execute **exactly the same code path**.

Import graph (single direction, no cycles at module level)::

    mathbank.classify_rules  ->  mathbank.question_types  ->  (re only)
    mathbank.classify_rules  ->  mathbank.tags            ->  mathbank.paths

``mathbank.question_types.detect_choice_options`` is kept as a thin legacy
wrapper; it resolves ``classify_rules`` lazily *inside the function body* so
the module-level import direction stays one-way.
"""

from __future__ import annotations

import re
from functools import lru_cache

from mathbank.question_types import (
    QUESTION_FORM_CHOICE,
    QUESTION_FORM_FILL_IN_BLANK,
    QUESTION_FORM_DETAILED_ANSWER,
    QUESTION_FORM_UNKNOWN,
    QUESTION_TYPE_DETAILED_ANSWER,
    QUESTION_TYPE_FILL_IN_BLANK,
    QUESTION_TYPE_MULTI_CHOICE,
    QUESTION_TYPE_SINGLE_CHOICE,
    QUESTION_TYPE_UNKNOWN,
    detect_structured_question_form,
    normalize_ai_question_form,
    normalize_ai_question_type,
)
from mathbank.tags import curriculum_index, node_path


# ---------------------------------------------------------------------------
# 0. Shared helpers
# ---------------------------------------------------------------------------

#: LaTeX command token.  Used to mask macro names (plus the horizontal space
#: right after them) so that math expressions such as ``\cup B)`` or
#: ``\parallel A`` can never look like an option marker.
_LATEX_COMMAND = re.compile(r"\\[a-zA-Z]+[ \t]*")


def _mask_latex_commands(text: str) -> str:
    """Replace every LaTeX command with a same-length non-space filler.

    Keeping the length identical preserves all positions (so line starts and
    column offsets stay meaningful) while making ``A`` in ``P(A)`` or
    ``\\cup B)`` invisible to the option scanner.
    """

    return _LATEX_COMMAND.sub(lambda m: "\x00" * len(m.group(0)), str(text or ""))


def _word_char(ch: str) -> bool:
    """Chinese / latin / digit / underscore -- i.e. "inside a word"."""

    return bool(re.match(r"[\w一-鿿]", ch))


# ---------------------------------------------------------------------------
# 1. Option analysis (replaces the old ``[A-D]\s*[.、)．:：）]\s*\S`` regex)
# ---------------------------------------------------------------------------

#: Punctuation that may legally sit *before* an option letter.
_PREV_PUNCT = set("：:）)]｜|】］」〕》〉（“\"‘'《〈「〔([［{")
#: Punctuation that may legally sit *after* an option letter.
_FOLLOW_PUNCT = set(".、)．:：，,;；]】］）〕」》〉）")
#: Circled / Chinese option markers used by OCR exports.
_CIRCLED_NUMBERS = "①②③④⑤⑥⑦⑧⑨⑩"
_CHINESE_MARKERS = "甲乙丙丁"

_OPTION_MACROS: tuple[tuple[re.Pattern[str], str, int], ...] = (
    # (pattern, evidence label, minimum number of occurrences)
    (re.compile(r"\\begin\s*\{\s*choices\s*\}", re.IGNORECASE), "macro:begin{choices}", 1),
    (re.compile(r"\\begin\s*\{\s*tasks\s*\}", re.IGNORECASE), "macro:begin{tasks}", 1),
    (re.compile(r"\\fourchoices\b", re.IGNORECASE), "macro:fourchoices", 1),
    (re.compile(r"\\twochoices\b", re.IGNORECASE), "macro:twochoices", 1),
    (re.compile(r"\\fourch\b", re.IGNORECASE), "macro:fourch", 1),
    (re.compile(r"\\xxchoise\b", re.IGNORECASE), "macro:xxchoise", 1),
    (re.compile(r"\\choice\b", re.IGNORECASE), "macro:choice", 2),
)

_ENUMERATE_ENV = re.compile(
    r"\\begin\s*\{\s*(?:enumerate|itemize|tasks|list)\s*\}", re.IGNORECASE
)
_ITEM_MACRO = re.compile(r"\\item\b", re.IGNORECASE)
_TASK_MACRO = re.compile(r"\\task\b", re.IGNORECASE)


def _prev_context_ok(text: str, index: int) -> bool:
    """Is ``text[index]`` allowed to start an option marker?"""

    if index <= 0:
        return True
    prev = text[index - 1]
    if prev == "\n" or prev.isspace():
        return True
    if prev in _PREV_PUNCT:
        # Reject function-call context: ``P(A)`` / ``f(A)`` / ``(A\cup``.
        # A real option looks like ``（A）1`` where the char before the
        # bracket is a space, a newline or another bracket.
        if prev in "(（" and index >= 2:
            before = text[index - 2]
            if before.isalnum() or before in ")）":
                return False
        return True
    # Chinese char, latin letter, digit, backslash, $, ^, _, { ... -> reject
    return False


def _follow_context_ok(text: str, index: int) -> str | None:
    """Return the separator style after an option letter (or ``None``)."""

    nxt = index + 1
    if nxt >= len(text):
        return "eol"
    char = text[nxt]
    if char in _FOLLOW_PUNCT:
        return "punct"
    if char.isspace():
        return "space"
    return None


def _scan_option_letters(masked: str) -> list[tuple[str, str]]:
    """Collect ``(letter, style)`` hits for A-D / a-d option markers."""

    hits: list[tuple[str, str]] = []
    for index, char in enumerate(masked):
        if char not in "ABCDabcd":
            continue
        if not _prev_context_ok(masked, index):
            continue
        style = _follow_context_ok(masked, index)
        if style is None:
            continue
        hits.append((char.upper(), style))
    return hits


def _longest_consecutive_run(letters: list[str]) -> int:
    """Length of the longest A→B→C… run (order = appearance order)."""

    values = [ord(ch) - 65 for ch in letters]
    if not values:
        return 0
    best = cur = 1
    for i in range(1, len(values)):
        if values[i] == values[i - 1] + 1:
            cur += 1
            best = max(best, cur)
        elif values[i] == values[i - 1]:
            continue
        else:
            cur = 1
    return best


def analyze_options(content: str) -> dict:
    """Detect A/B/C/D style options with position + quantity constraints.

    Returns ``{"has_options", "letters", "count", "styles", "evidence"}``.
    """

    raw = str(content or "")
    masked = _mask_latex_commands(raw)
    evidence: list[str] = []
    styles: list[str] = []

    # (f) LaTeX macros -- strongest signal, no visible letters required.
    for pattern, label, minimum in _OPTION_MACROS:
        found = len(pattern.findall(raw))
        if found >= minimum:
            evidence.append(f"{label}×{found}")
    if _ENUMERATE_ENV.search(raw) and len(_ITEM_MACRO.findall(raw)) >= 3:
        evidence.append(f"macro:enumerate+item×{len(_ITEM_MACRO.findall(raw))}")
    task_count = len(_TASK_MACRO.findall(raw))
    if task_count >= 3:
        evidence.append(f"macro:task×{task_count}")

    # (a)(b)(c)(d) letter scanning.
    hits = _scan_option_letters(masked)
    letters = [letter for letter, _ in hits]
    punct_letters = {l for l, style in hits if style == "punct"}
    space_letters = [l for l, style in hits if style == "space"]

    if punct_letters:
        styles.append("punct")
        evidence.append("letters:" + "".join(sorted(punct_letters)))
    if space_letters:
        styles.append("space")
        evidence.append("spaced-letters:" + "".join(space_letters))

    run = _longest_consecutive_run(space_letters)
    if run >= 3:
        evidence.append(f"spaced-run:{run}")

    # (e) non-latin markers.
    circled = sorted({ch for ch in _CIRCLED_NUMBERS if ch in raw})
    if len(circled) >= 3:
        evidence.append("circled:" + "".join(circled))
    chinese = [ch for ch in _CHINESE_MARKERS if re.search(ch + r"\s*[.、．:：]", raw)]
    if len(chinese) >= 3:
        evidence.append("chinese:" + "".join(chinese))

    has_options = bool(evidence) and (
        bool(punct_letters) and len(punct_letters) >= 2
        or run >= 3
        or len(circled) >= 3
        or len(chinese) >= 3
        or any(item.startswith("macro:") for item in evidence)
    )

    return {
        "has_options": has_options,
        "letters": sorted(set(letters)),
        "count": len(set(letters)),
        "styles": styles,
        "evidence": evidence,
    }


def _strong_option_evidence(options: dict) -> bool:
    """Is the option evidence strong enough to be called *structure*?

    ``detect_structured_question_form`` is expected to be conservative, so a
    single stray letter is not enough: we require an explicit option macro, a
    non-latin marker set, or at least three distinct A-D letters.
    """

    for item in options["evidence"]:
        if item.startswith(("macro:", "circled:", "chinese:", "spaced-run:")):
            return True
    for item in options["evidence"]:
        if item.startswith("letters:"):
            return len(set(item[len("letters:"):])) >= 3
    return False


def structured_form_from_evidence(content: str) -> str | None:
    """Extended structural detection used by ``detect_structured_question_form``.

    Kept here (not in ``question_types``) so that the option scanner and the
    blank scanner have exactly one implementation.
    """

    raw = str(content or "")
    options = analyze_options(raw)
    if options["has_options"] and _strong_option_evidence(options):
        return QUESTION_FORM_CHOICE
    if detect_blank_slots(raw, has_options=options["has_options"]):
        return QUESTION_FORM_FILL_IN_BLANK
    return None


def detect_choice_options(content: str) -> bool:
    """Legacy boolean wrapper kept for callers that only need yes/no."""

    return analyze_options(content)["has_options"]


# ---------------------------------------------------------------------------
# 2. Blank slot detection (fill-in-the-blank evidence)
# ---------------------------------------------------------------------------

_BLANK_MACRO = re.compile(
    r"\\(?:fillin|fillinblank|fillinblankline|makeblank|blank|ansbox)"
    r"(?:\s*(?:\[[^\]]*\]|\{[^}]*\}))?",
    re.IGNORECASE,
)
_BLANK_UNDERLINE = re.compile(
    r"\\underline\s*\{\s*(?:\\(?:quad|qquad|hspace|hfill|phantom|kern)\s*"
    r"(?:\{[^{}]*\})?|\s*|[　\s]{1,})\s*\}",
    re.IGNORECASE,
)
_BLANK_HSPACE = re.compile(r"\\[hv]space\s*(?:\*[^{}]*)?\{[^{}]*\}", re.IGNORECASE)
_UNDERSCORE_RUN = re.compile(r"_{3,}|＿{2,}|ˍ{2,}|＿+")
_PAREN_BLANK = re.compile(r"[（(［\[]\s*(?:[　]{2,}|[ \t]{4,})\s*[)）］\]]")
_BLANK_HINTS = (
    "把答案填在",
    "填在横线上",
    "填在题中的横线上",
    "将答案填入",
    "将答案填写",
    "空白处应填",
    "请在横线上填写",
    "填在题中",
)


def detect_blank_slots(content: str, has_options: bool | None = None) -> bool:
    """Return True when the stem contains an explicit answer slot.

    ``has_options`` lets the caller pass already-computed option evidence.
    When a question clearly has A/B/C/D options, a trailing ``（　　）`` is
    the *choice answer bracket*, not a fill-in slot, so it is ignored.
    """

    raw = str(content or "")
    if has_options is None:
        has_options = analyze_options(raw)["has_options"]

    if _BLANK_MACRO.search(raw):
        return True
    if _BLANK_UNDERLINE.search(raw):
        return True
    if _BLANK_HSPACE.search(raw):
        return True
    if _UNDERSCORE_RUN.search(raw):
        return True
    if _PAREN_BLANK.search(raw) and not has_options:
        return True
    if any(hint in raw for hint in _BLANK_HINTS):
        return True
    return False


# ---------------------------------------------------------------------------
# 3. Multi-choice signals (reuses the判据 already present in source_metadata)
# ---------------------------------------------------------------------------

_MULTI_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"多选题|多项选择题|多项选择|（多选）|\(多选\)|多选\b", "label:多选题"),
    (r"在每小题给出的四个选项中[^。；\n]{0,12}(?:有多项是符合题目要求的|有多项符合题目要求|有多个选项是符合题目要求的)", "phrase:在每小题给出的四个选项中…有多项"),
    (r"有多项符合题目要求|有多个选项正确|有多个选项符合|有多个正确选项|不止一个正确|不止一项正确", "phrase:有多项符合"),
    (r"选出所有满足条件|所有满足条件的|选出所有|全部满足条件", "phrase:选出所有满足条件的"),
    (r"正确的个数|正确选项的个数|正确选项共有", "phrase:正确的个数"),
    (r"全部选对(?:的)?得\s*\d|部分选对(?:的)?得\s*\d|选对一部分(?:的)?得\s*\d", "scoring:全部选对/部分选对"),
    (r"部分选对的得部分分|对而不全|选对但不全|漏选", "scoring:部分分"),
)


def multi_choice_signals(content: str) -> list[str]:
    """Return the list of multi-choice hints found in the content."""

    raw = str(content or "")
    return [label for pattern, label in _MULTI_SIGNAL_PATTERNS if re.search(pattern, raw)]


def detect_multi_choice_signals(content: str) -> bool:
    return bool(multi_choice_signals(content))


# ---------------------------------------------------------------------------
# 4. Sub-questions and parameter discussion (difficulty priors)
# ---------------------------------------------------------------------------

_SUBQUESTION_PATTERN = re.compile(r"[（(]\s*(\d{1,2}|[一二三四五六七八九十]|[①-⑩])\s*[)）]")
_CIRCLED_TO_INT = {ch: idx + 1 for idx, ch in enumerate(_CIRCLED_NUMBERS)}
_CN_NUMBERS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
               "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}


def detect_subquestion_count(content: str) -> int:
    """Count numbered sub-questions ``（1）（2）（3）`` / ``(1)(2)`` / ``①②③``."""

    raw = str(content or "")
    found: set[int] = set()
    for match in _SUBQUESTION_PATTERN.finditer(raw):
        token = match.group(1)
        if token.isdigit():
            found.add(int(token))
        elif token in _CIRCLED_TO_INT:
            found.add(_CIRCLED_TO_INT[token])
        elif token in _CN_NUMBERS:
            found.add(_CN_NUMBERS[token])
    for index, ch in enumerate(_CIRCLED_NUMBERS, start=1):
        if ch in raw:
            found.add(index)
    count = 0
    while count + 1 in found:
        count += 1
    return count


_DISCUSSION_WORDS = ("讨论", "是否存在", "取值范围", "恒成立", "对任意", "探究", "求证", "证明")
_PARAM_PATTERNS = (
    r"[a-zA-Zλ]\s*(?:\\in|∈|≥|≤|>|<|≠)",
    r"(?:实数|参数|常数)\s*[a-zA-Zλ]",
    r"[a-zA-Z]\s*[∈≥≤><]\s*\d",
)


def detect_parameter_discussion(content: str) -> bool:
    """True when the stem asks for a parameter discussion / existence proof."""

    raw = str(content or "")
    masked = _mask_latex_commands(raw)
    if not any(word in raw for word in _DISCUSSION_WORDS):
        return False
    if any(re.search(pattern, masked) or re.search(pattern, raw) for pattern in _PARAM_PATTERNS):
        return True
    return "证明" in raw or "求证" in raw


# ---------------------------------------------------------------------------
# 5. Question type decision (the single entry point)
# ---------------------------------------------------------------------------

def decide_question_type(content: str, ai_value=None) -> tuple[str, str, dict]:
    """Decide the four-value question type.

    Returns ``(question_type, source, evidence)`` where
    ``source ∈ {structure, ai, rule, corrected, fallback}``.
    """

    raw = str(content or "")
    structure = detect_structured_question_form(raw)
    options = analyze_options(raw)
    blanks = detect_blank_slots(raw, has_options=options["has_options"])
    multi = detect_multi_choice_signals(raw)
    ai_type = normalize_ai_question_type(ai_value)
    coarse = normalize_ai_question_form(ai_value)

    evidence = {
        "options": options,
        "blanks": blanks,
        "multi_signals": multi_choice_signals(raw),
        "structure": structure,
        "ai_type": ai_type,
        "ai_coarse": coarse,
    }

    def choice_kind(source_hint: str) -> tuple[str, str]:
        return (QUESTION_TYPE_MULTI_CHOICE if multi else QUESTION_TYPE_SINGLE_CHOICE), source_hint

    # 1. Structural macros are the strongest evidence.
    if structure == QUESTION_FORM_CHOICE:
        kind, _ = choice_kind("structure")
        return kind, "structure", evidence
    if structure == QUESTION_FORM_FILL_IN_BLANK:
        return QUESTION_TYPE_FILL_IN_BLANK, "structure", evidence

    # 2. + 3. Normalized AI value, corrected by local evidence.
    if ai_type == QUESTION_TYPE_SINGLE_CHOICE or ai_type == QUESTION_TYPE_MULTI_CHOICE:
        if options["has_options"]:
            return choice_kind("ai")
        if blanks:
            return QUESTION_TYPE_FILL_IN_BLANK, "corrected", evidence
        return QUESTION_TYPE_DETAILED_ANSWER, "corrected", evidence

    if ai_type == QUESTION_TYPE_FILL_IN_BLANK:
        if options["has_options"] and not blanks:
            return choice_kind("corrected")
        if not blanks and not options["has_options"]:
            # AI claims a blank but the stem has neither slots nor options.
            return QUESTION_TYPE_DETAILED_ANSWER, "corrected", evidence
        return QUESTION_TYPE_FILL_IN_BLANK, "ai", evidence

    if ai_type == QUESTION_TYPE_DETAILED_ANSWER:
        if options["has_options"] and not blanks:
            return choice_kind("corrected")
        return QUESTION_TYPE_DETAILED_ANSWER, "ai", evidence

    # AI only said "choice" (coarse) -- resolve with rules, never guess silently.
    if coarse == QUESTION_FORM_CHOICE:
        if options["has_options"]:
            return choice_kind("ai")
        if blanks:
            return QUESTION_TYPE_FILL_IN_BLANK, "corrected", evidence
        return QUESTION_TYPE_DETAILED_ANSWER, "corrected", evidence

    # 4. No usable AI value: fall back to pure rule evidence.
    if options["has_options"]:
        return choice_kind("rule")
    if blanks:
        return QUESTION_TYPE_FILL_IN_BLANK, "rule", evidence
    return QUESTION_TYPE_UNKNOWN, "fallback", evidence


def question_type_to_form(question_type: str) -> str:
    """Collapse the four-value type back to the legacy coarse form."""

    if question_type in (QUESTION_TYPE_SINGLE_CHOICE, QUESTION_TYPE_MULTI_CHOICE):
        return QUESTION_FORM_CHOICE
    if question_type == QUESTION_TYPE_FILL_IN_BLANK:
        return QUESTION_FORM_FILL_IN_BLANK
    if question_type == QUESTION_TYPE_DETAILED_ANSWER:
        return QUESTION_FORM_DETAILED_ANSWER
    return QUESTION_FORM_UNKNOWN


# ---------------------------------------------------------------------------
# 6. Difficulty prior
# ---------------------------------------------------------------------------

_LEVELS = {"easy": 0, "medium": 1, "hard": 2}
_LEVEL_NAMES = {0: "easy", 1: "medium", 2: "hard"}

_HARD_WORDS = ("证明", "求证", "探究", "是否存在", "新定义", "恒成立", "讨论", "归纳")
_MEDIUM_WORDS = ("最大值", "最小值", "最值", "极值", "取值范围", "单调区间", "恒成立", "切线")


def estimate_difficulty_prior(content: str) -> tuple[str, list[str]]:
    """Return ``(easy|medium|hard, evidence)`` from structural signals only."""

    raw = str(content or "")
    signals: list[str] = []
    score = 0

    subquestions = detect_subquestion_count(raw)
    if subquestions >= 3:
        score += 2
        signals.append(f"小问数≥3（{subquestions}）")
    elif subquestions == 2:
        score += 1
        signals.append("小问数=2")

    param_discussion = detect_parameter_discussion(raw)
    if param_discussion:
        score += 2
        signals.append("含参讨论/存在性")

    hard_words = [word for word in _HARD_WORDS if word in raw]
    if hard_words:
        score += 1
        signals.append("设问词:" + "/".join(hard_words))

    medium_words = [word for word in _MEDIUM_WORDS if word in raw]
    if medium_words:
        score += 1
        signals.append("关键词:" + "/".join(medium_words))

    options = analyze_options(raw)
    if options["has_options"] and detect_multi_choice_signals(raw):
        score += 2
        signals.append("多选题（需逐项判断）")

    # 解析几何 / 导数 / 数列 综合题：多小问时几乎必然是中档以上
    if subquestions >= 2 and re.search(r"椭圆|双曲线|抛物线|导数|数学归纳法|空间向量", raw):
        score += 1
        signals.append("综合模块多小问")

    length = len(raw)
    if length > 300:
        score += 1
        signals.append(f"题干较长（{length}字）")
    elif length < 60:
        score -= 1
        signals.append(f"题干很短（{length}字）")

    candidates = suggest_chapter_candidates(raw, top_n=3)
    books = {code.split("-")[0] for code, _ in candidates}
    if len(books) >= 2:
        score += 1
        signals.append("疑似跨册融合")

    if score >= 4:
        level = 2
    elif score >= 2:
        level = 1
    else:
        level = 0
    return _LEVEL_NAMES[level], signals


# ---------------------------------------------------------------------------
# 7. Chapter candidate retrieval
# ---------------------------------------------------------------------------

_NODE_CLEAN = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|第[一二三四五六七八九十]+[章节])\s*")
_NODE_SPLIT = re.compile(r"[与和、/（(）),，·]|的应用|及其")

#: Curated high-frequency 人教A版2019 terms -> node codes.
_TERM_HINTS: dict[str, tuple[str, ...]] = {
    "集合": ("B1-C1", "B1-C1-S1", "B1-C1-S2", "B1-C1-S3"),
    "交集": ("B1-C1-S3",), "并集": ("B1-C1-S3",), "补集": ("B1-C1-S3",),
    "子集": ("B1-C1-S2",), "全集": ("B1-C1-S3",),
    "充分条件": ("B1-C1-S4-P1",), "必要条件": ("B1-C1-S4-P1",), "充要条件": ("B1-C1-S4-P2",),
    "全称量词": ("B1-C1-S5-P1",), "存在量词": ("B1-C1-S5-P1",), "命题的否定": ("B1-C1-S5-P2",),
    "基本不等式": ("B1-C2-S2",), "不等式": ("B1-C2", "B1-C2-S1", "B1-C2-S3"),
    "一元二次": ("B1-C2-S3",),
    "定义域": ("B1-C3-S1",), "值域": ("B1-C3-S1",), "解析式": ("B1-C3-S1-P2",),
    "单调性": ("B1-C3-S2-P1", "X2-C5-S3-P1"), "奇函数": ("B1-C3-S2-P2",), "偶函数": ("B1-C3-S2-P2",),
    "奇偶性": ("B1-C3-S2-P2",), "幂函数": ("B1-C3-S3",),
    "指数函数": ("B1-C4-S2",), "分数指数幂": ("B1-C4-S1-P1",), "无理数指数幂": ("B1-C4-S1-P2",),
    "对数函数": ("B1-C4-S4",), "对数": ("B1-C4-S3", "B1-C4-S3-P2"),
    "任意角": ("B1-C5-S1",), "弧度制": ("B1-C5-S1",),
    "终边": ("B1-C5-S2",), "三角函数": ("B1-C5", "B1-C5-S2", "B1-C5-S4"),
    "诱导公式": ("B1-C5-S3",), "最小正周期": ("B1-C5-S4", "B1-C5-S6"),
    "三角恒等变换": ("B1-C5-S5",), "二倍角": ("B1-C5-S5",), "和角": ("B1-C5-S5",),
    "振幅": ("B1-C5-S6",), "相位": ("B1-C5-S6",), "ωx": ("B1-C5-S6",),
    "向量": ("B2-C6", "B2-C6-S2", "B2-C6-S3", "X1-C1"),
    "数量积": ("B2-C6-S2", "X1-C1-S1-P2"), "共线": ("B2-C6-S2",), "垂直": ("B2-C6-S2",),
    "平面向量基本定理": ("B2-C6-S3",), "坐标表示": ("B2-C6-S3",),
    "余弦定理": ("B2-C6-S4",), "正弦定理": ("B2-C6-S4",), "解三角形": ("B2-C6-S4",),
    "复数": ("B2-C7",), "虚部": ("B2-C7-S1",), "实部": ("B2-C7-S1",), "共轭": ("B2-C7-S2",),
    "棱柱": ("B2-C8-S1",), "棱锥": ("B2-C8-S1",), "棱台": ("B2-C8-S1",), "圆台": ("B2-C8-S1",),
    "直观图": ("B2-C8-S2",), "表面积": ("B2-C8-S3",), "体积": ("B2-C8-S3",),
    "异面直线": ("B2-C8-S4",), "线面平行": ("B2-C8-S5",), "线面垂直": ("B2-C8-S6",),
    "二面角": ("B2-C8-S6",), "所成角": ("X1-C1-S4-P2",),
    "随机抽样": ("B2-C9-S1",), "频率分布": ("B2-C9-S2",), "样本": ("B2-C9-S2",),
    "平均数": ("B2-C9-S2",), "中位数": ("B2-C9-S2",), "众数": ("B2-C9-S2",), "方差": ("B2-C9-S2",),
    "互斥": ("B2-C10-S1",), "对立事件": ("B2-C10-S1",), "古典概型": ("B2-C10-S1",),
    "相互独立": ("B2-C10-S2",), "独立性": ("B2-C10-S2",),
    "空间向量": ("X1-C1",), "空间直角坐标系": ("X1-C1-S3-P1",),
    "倾斜角": ("X1-C2-S1",), "斜率": ("X1-C2-S1",),
    "直线的方程": ("X1-C2-S2",), "点斜式": ("X1-C2-S2-P1",), "一般式": ("X1-C2-S2-P3",),
    "点到直线的距离": ("X1-C2-S3-P3",), "平行直线间的距离": ("X1-C2-S3-P4",),
    "圆的方程": ("X1-C2-S4",), "圆的标准方程": ("X1-C2-S4-P1",), "圆的一般方程": ("X1-C2-S4-P2",),
    "弦长": ("X1-C2-S5-P1",), "相切": ("X1-C2-S5-P1",), "圆与圆": ("X1-C2-S5-P2",),
    "椭圆": ("X1-C3-S1",), "双曲线": ("X1-C3-S2",), "抛物线": ("X1-C3-S3",),
    "离心率": ("X1-C3-S1-P2", "X1-C3-S2-P2"),
    "数列": ("X2-C4",), "通项公式": ("X2-C4-S1",),
    "等差数列": ("X2-C4-S2",), "公差": ("X2-C4-S2-P1",), "前n项和": ("X2-C4-S2-P2", "X2-C4-S3-P2"),
    "等比数列": ("X2-C4-S3",), "公比": ("X2-C4-S3-P1",),
    "数学归纳法": ("X2-C4-S4",),
    "导数": ("X2-C5", "X2-C5-S1", "X2-C5-S2", "X2-C5-S3"),
    "求导": ("X2-C5-S2",), "复合函数": ("X2-C5-S2-P3",),
    "极值": ("X2-C5-S3-P2",), "极大值": ("X2-C5-S3-P2",), "极小值": ("X2-C5-S3-P2",),
    "单调区间": ("X2-C5-S3-P1", "B1-C3-S2-P1"),
    "计数原理": ("X3-C6-S1",), "排列": ("X3-C6-S2-P1",), "组合": ("X3-C6-S2-P3",),
    "二项式定理": ("X3-C6-S3-P1",), "展开式": ("X3-C6-S3-P1",), "二项式系数": ("X3-C6-S3-P2",),
    "条件概率": ("X3-C7-S1-P1",), "全概率": ("X3-C7-S1-P2",),
    "分布列": ("X3-C7-S2",), "离散型随机变量": ("X3-C7-S2",),
    "数学期望": ("X3-C7-S3-P1",), "期望": ("X3-C7-S3-P1",), "方差": ("X3-C7-S3-P2",),
    "二项分布": ("X3-C7-S4-P1",), "超几何分布": ("X3-C7-S4-P2",), "正态分布": ("X3-C7-S5",),
    "线性回归": ("X3-C8-S2",), "回归": ("X3-C8-S2",), "列联表": ("X3-C8-S3",), "独立性检验": ("X3-C8-S3",),
}

#: Surface notation -> chapter hints.  OCR / Word exports often lose the
#: Chinese topic words entirely (``f(x)=a^{x}`` never says 指数函数), so a
#: small notation table keeps the retriever from returning nothing.
_SURFACE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"a\^\{?\s*x\s*\}?|\\?\^ ?\{?x\}?\}", ("B1-C4-S2",)),
    (r"\\log|\blog|\blg\s|\bln\s", ("B1-C4-S3", "B1-C4-S4")),
    (r"\\sin|\\cos|\\tan|\bsin|\bcos|\btan", ("B1-C5-S2", "B1-C5-S4")),
    (r"\\vec|\\overrightarrow|向量", ("B2-C6", "X1-C1")),
    (r"\{a_?\{?n\}?\}|a_\{?n\b|S_\{?n\}?|前\s*n\s*项和", ("X2-C4",)),
    (r"导数|f'\s*\(|\\mathrm\{d\}", ("X2-C5",)),
    (r"椭圆|双曲线|抛物线|离心率", ("X1-C3",)),
    (r"∥|平行于", ("B2-C8-S5", "X1-C1-S4-P1")),
    (r"⊥|垂直于", ("B2-C8-S6", "X1-C1-S4-P1")),
    (r"\bP\s*\(|概率|\bC_\{?\d", ("B2-C10-S1", "X3-C7-S2")),
    (r"排列|组合|展开式|二项式", ("X3-C6",)),
    (r"复数|虚部|实部", ("B2-C7",)),
    (r"棱柱|棱锥|棱台|圆台|四棱锥|三棱柱", ("B2-C8-S1",)),
    (r"平均数|中位数|众数|频率分布", ("B2-C9-S2",)),
    (r"△|对边|解三角形|余弦定理|正弦定理", ("B2-C6-S4",)),
    # a + k/a 与 1/a + 1/b 是「基本不等式」的标志性外形
    (r"[a-z]\s*\+\s*\d\s*/\s*[a-z]|\d\s*/\s*[a-z]\s*\+\s*\d\s*/\s*[a-z]", ("B1-C2-S2",)),
    (r"f\s*\(\s*x\s*\)|函数", ("B1-C3",)),
)

_LEVEL_WEIGHT = {"book": 0.6, "chapter": 1.0, "section": 1.2, "subsection": 1.3}


@lru_cache(maxsize=1)
def _node_children() -> dict[str, tuple[str, ...]]:
    children: dict[str, list[str]] = {}
    for code in curriculum_index("A"):
        if "-" in code:
            children.setdefault(code.rsplit("-", 1)[0], []).append(code)
    return {parent: tuple(sorted(kids)) for parent, kids in children.items()}


@lru_cache(maxsize=1)
def _auto_terms() -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Extract searchable terms from every curriculum node title."""

    bucket: dict[str, set[str]] = {}
    for code, node in curriculum_index("A").items():
        name = _NODE_CLEAN.sub("", str(node.get("name", "")))
        name = name.replace("*", "").strip()
        if not name:
            continue
        for piece in [name, *_NODE_SPLIT.split(name)]:
            piece = piece.strip()
            if len(piece) >= 2:
                bucket.setdefault(piece, set()).add(code)
    return tuple((term, tuple(sorted(codes))) for term, codes in bucket.items())


def suggest_chapter_candidates(content: str, top_n: int = 8) -> list[tuple[str, str]]:
    """Score every curriculum node against the question text.

    Returns up to ``top_n`` ``(code, path)`` pairs, best first.
    """

    raw = str(content or "")
    if not raw.strip():
        return []
    index = curriculum_index("A")
    children = _node_children()
    scores: dict[str, float] = {}

    def bump(code: str, weight: float) -> None:
        node = index.get(code)
        if not node:
            return
        scores[code] = scores.get(code, 0.0) + weight * _LEVEL_WEIGHT.get(node["level"], 1.0)
        # A term that matches a section should also suggest its subsections
        # (and, more weakly, its chapter), because a keyword rarely carries
        # enough signal to pick the exact sub-node.
        parent = code.rsplit("-", 1)[0] if "-" in code else ""
        step = weight * 0.35
        for _ in range(2):
            if not parent or parent not in index:
                break
            scores[parent] = scores.get(parent, 0.0) + step * _LEVEL_WEIGHT.get(
                index[parent]["level"], 1.0
            )
            parent = parent.rsplit("-", 1)[0] if "-" in parent else ""
            step *= 0.5
        level_nodes = [(code, weight * 0.45)]
        for _ in range(2):
            nxt: list[tuple[str, float]] = []
            for node_code, node_weight in level_nodes:
                for kid in children.get(node_code, ()):
                    if kid in index:
                        scores[kid] = scores.get(kid, 0.0) + node_weight * _LEVEL_WEIGHT.get(
                            index[kid]["level"], 1.0
                        )
                        nxt.append((kid, node_weight * 0.45))
            level_nodes = nxt

    for term, codes in _TERM_HINTS.items():
        if term in raw:
            weight = (len(term) ** 1.5) * 1.6
            for code in codes:
                bump(code, weight)
    for term, codes in _auto_terms():
        if term in raw:
            weight = len(term) ** 1.5
            for code in codes:
                bump(code, weight)
    for pattern, codes in _SURFACE_HINTS:
        if re.search(pattern, raw):
            weight = 4 ** 1.5
            for code in codes:
                bump(code, weight)

    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    return [(code, node_path(code, "A")) for code, _ in ranked[:top_n]]


# ---------------------------------------------------------------------------
# 8. Unified fusion entry point
# ---------------------------------------------------------------------------

def classify_with_rules(content: str, ai_payload: dict | None = None) -> dict:
    """Fuse model output with rule evidence into one response payload."""

    payload = ai_payload if isinstance(ai_payload, dict) else {}
    raw = str(content or "")

    ai_value = payload.get("question_type", payload.get("question_form"))
    question_type, type_source, decision = decide_question_type(raw, ai_value)
    question_form = question_type_to_form(question_type)

    options = decision["options"]
    subquestions = detect_subquestion_count(raw)
    param_discussion = detect_parameter_discussion(raw)
    prior, prior_signals = estimate_difficulty_prior(raw)

    ai_difficulty = str(payload.get("difficulty", "") or "").strip().lower()
    if ai_difficulty in _LEVELS:
        difficulty = ai_difficulty
        difficulty_source = "ai"
        difficulty_conflict = abs(_LEVELS[difficulty] - _LEVELS[prior]) >= 2
    else:
        difficulty = prior
        difficulty_source = "prior"
        difficulty_conflict = False

    ai_code = str(payload.get("chapter_code", "") or "").strip()
    index = curriculum_index("A")
    if ai_code and ai_code in index:
        chapter_code = ai_code
        chapter_path = node_path(ai_code, "A")
        chapter_source = "ai"
    else:
        candidates = suggest_chapter_candidates(raw, top_n=8)
        if candidates:
            chapter_code, chapter_path = candidates[0]
            chapter_source = "rule"
        else:
            chapter_code, chapter_path, chapter_source = "", "", "fallback"

    needs_review = bool(
        difficulty_conflict
        or question_type == QUESTION_TYPE_UNKNOWN
        or chapter_source != "ai"
        or difficulty_source == "prior"
    )

    return {
        "question_type": question_type,
        "question_type_source": type_source,
        "question_form": question_form,
        "question_form_source": type_source,
        "difficulty": difficulty,
        "difficulty_source": difficulty_source,
        "difficulty_conflict": difficulty_conflict,
        "difficulty_prior": prior,
        "chapter_code": chapter_code,
        "chapter_path": chapter_path,
        "chapter_source": chapter_source,
        "evidence": {
            "options": options,
            "blanks": decision["blanks"],
            "multi_signals": decision["multi_signals"],
            "subquestions": subquestions,
            "param_discussion": param_discussion,
            "difficulty_signals": prior_signals,
        },
        "needs_review": needs_review,
        "is_fallback": question_type == QUESTION_TYPE_UNKNOWN or chapter_source == "fallback",
    }
