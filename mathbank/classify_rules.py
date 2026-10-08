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

#: (pattern, teacher-facing Chinese label, machine-readable macro name, minimum hits)
#: The Chinese label is rendered verbatim by the frontend; the macro name is
#: only exposed through ``analyze_options()["macros"]`` for debugging.
_OPTION_MACROS: tuple[tuple[re.Pattern[str], str, str, int], ...] = (
    (re.compile(r"\\begin\s*\{\s*choices\s*\}", re.IGNORECASE),
     "LaTeX 选择题排版环境", r"\begin{choices}", 1),
    (re.compile(r"\\begin\s*\{\s*tasks\s*\}", re.IGNORECASE),
     "LaTeX 选项列表环境", r"\begin{tasks}", 1),
    (re.compile(r"\\fourchoices\b", re.IGNORECASE),
     "LaTeX 四选一排版命令", r"\fourchoices", 1),
    (re.compile(r"\\twochoices\b", re.IGNORECASE),
     "LaTeX 二选一排版命令", r"\twochoices", 1),
    (re.compile(r"\\fourch\b", re.IGNORECASE),
     "LaTeX 四选项排版命令", r"\fourch", 1),
    (re.compile(r"\\xxchoise\b", re.IGNORECASE),
     "LaTeX 选项排版命令", r"\xxchoise", 1),
    (re.compile(r"\\choice\b", re.IGNORECASE),
     "LaTeX 选项排版命令", r"\choice", 2),
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
    # Machine-readable flags -- the frontend never needs these, they exist so
    # callers do not have to string-parse ``evidence``.
    macro_hit = False
    marker_hit = False
    spaced_run = 0

    # (f) LaTeX macros -- strongest signal, no visible letters required.
    macros: list[str] = []
    for pattern, label, macro_name, minimum in _OPTION_MACROS:
        found = len(pattern.findall(raw))
        if found >= minimum:
            macro_hit = True
            macros.append(macro_name)
            evidence.append(f"识别到{label}（{found} 处）")
    # NOTE: 4 (not 3) is the bar for "this list is a set of options".  Chinese
    # exams always use four options A/B/C/D (or ①②③④), while a 解答题 with
    # three sub-questions (1)(2)(3) is by far the most common false positive.
    item_count = len(_ITEM_MACRO.findall(raw))
    if _ENUMERATE_ENV.search(raw) and item_count >= 4:
        macro_hit = True
        macros.append(r"\begin{enumerate}+\item")
        evidence.append(f"识别到 LaTeX 列表环境，含 {item_count} 个选项条目")
    task_count = len(_TASK_MACRO.findall(raw))
    if task_count >= 4:
        macro_hit = True
        macros.append(r"\task")
        evidence.append(f"识别到 {task_count} 个 LaTeX 选项条目")

    # (a)(b)(c)(d) letter scanning.
    hits = _scan_option_letters(masked)
    letters = [letter for letter, _ in hits]
    punct_letters = sorted({l for l, style in hits if style == "punct"})
    space_letters = [l for l, style in hits if style == "space"]

    if punct_letters:
        evidence.append(f"识别到选项字母 {'、'.join(punct_letters)}")
    if space_letters:
        evidence.append(f"识别到无分隔选项字母 {'、'.join(sorted(set(space_letters)))}")

    spaced_run = _longest_consecutive_run(space_letters)
    if spaced_run >= 3:
        evidence.append(f"无分隔符但字母连续递增，共 {spaced_run} 个")

    # (e) non-latin markers.  Same "four options" convention as above: ①②③
    # used as sub-question numbering is far more common than a three-option
    # question, so three markers alone never proves a choice question.
    circled = sorted({ch for ch in _CIRCLED_NUMBERS if ch in raw})
    if len(circled) >= 4:
        marker_hit = True
        evidence.append(f"识别到圈码选项 {'、'.join(circled)}")
    # 天干选项后面跟的一定是句点类分隔符（甲．乙．丙．丁．）；纯顿号列举
    # 「甲、乙、丙、丁、戊五名同学」是题干叙述，不是选项。
    chinese = [ch for ch in _CHINESE_MARKERS if re.search(ch + r"\s*[.．:：]", raw)]
    if len(chinese) >= 3:
        marker_hit = True
        evidence.append(f"识别到天干选项 {'、'.join(chinese)}")

    has_options = bool(
        (len(punct_letters) >= 2)
        or spaced_run >= 3
        or len(circled) >= 4
        or len(chinese) >= 3
        or macro_hit
    )

    styles: list[str] = []
    if macro_hit:
        styles.append("macro")
    if punct_letters:
        styles.append("letter")
    if marker_hit:
        styles.append("marker")
    if spaced_run >= 3:
        styles.append("spaced")

    # 同一条证据可能被多个宏命中，去重后再交给前端展示
    seen: set[str] = set()
    unique_evidence = [item for item in evidence
                       if not (item in seen or seen.add(item))]

    return {
        "has_options": has_options,
        "letters": sorted(set(letters)),
        "count": len(set(letters)),
        "styles": styles,
        "punct_letters": punct_letters,
        "macro": macro_hit,
        "marker": marker_hit,
        "spaced_run": spaced_run,
        "macros": macros,
        "evidence": unique_evidence,
    }


def _strong_option_evidence(options: dict) -> bool:
    """Is the option evidence strong enough to be called *structure*?

    ``detect_structured_question_form`` is expected to be conservative, so a
    single stray letter is not enough: we require an explicit option macro, a
    non-latin marker set, or at least three distinct A-D letters.
    """

    if options["macro"] or options["marker"]:
        return True
    if options["spaced_run"] >= 3:
        return True
    return len(options["punct_letters"]) >= 3


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

#: NOTE: the labels below are shown to teachers verbatim by the frontend,
#: so they must stay readable Chinese -- never English keys or regex source.
_MULTI_SIGNAL_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"多选题|多项选择题|多项选择|（多选）|\(多选\)|多选\b", "题干标注「多选题」"),
    (r"在每小题给出的四个选项中[^。；\n]{0,12}(?:有多项是符合题目要求的|有多项符合题目要求|有多个选项是符合题目要求的)",
     "出现「在每小题给出的四个选项中，有多项符合题目要求」"),
    (r"有多项符合题目要求|有多个选项正确|有多个选项符合|有多个正确选项|不止一个正确|不止一项正确",
     "出现「有多项符合题目要求」"),
    (r"选出所有满足条件|所有满足条件的|选出所有|全部满足条件", "出现「选出所有满足条件的」"),
    (r"正确的个数|正确选项的个数|正确选项共有", "问的是「正确选项的个数」"),
    (r"全部选对(?:的)?得\s*\d|部分选对(?:的)?得\s*\d|选对一部分(?:的)?得\s*\d",
     "评分语含「全部选对得满分、部分选对得部分分」"),
    (r"部分选对的得部分分|对而不全|选对但不全|漏选", "评分语含「部分选对的得部分分」"),
)


def multi_choice_signals(content: str) -> list[str]:
    """Return the list of multi-choice hints found in the content."""

    raw = str(content or "")
    return [label for pattern, label in _MULTI_SIGNAL_PATTERNS if re.search(pattern, raw)]


def detect_multi_choice_signals(content: str) -> bool:
    return bool(multi_choice_signals(content))


# 「下列结论正确的是（　　）」这类设问在高考里单选、多选都会用，题干本身就分不出来。
# 所以这里**不拿它去判定多选**（那会变成对着评测集硬凑），只用来标记「这条真的分不清」，
# 交给老师点一下。最坏情况是多点一次，最好的情况是拦下一道静默入库的错题。
_AMBIGUOUS_CHOICE_STEM = re.compile(
    r"下列[^。；\n]{0,30}(?:正确|成立|符合|恰当|合理)[^。；\n]{0,8}(?:的是|的有|的选项)"
)


def detect_ambiguous_choice_stem(content: str) -> bool:
    """「下列…正确的是」式设问 —— 单选/多选无法从题干区分。"""

    return bool(_AMBIGUOUS_CHOICE_STEM.search(str(content or "")))


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
    # 圈码既可能是多选/单选的选项标号（①②③④），也可能是解答题的小问编号。
    # 若选项扫描已把圈码认定为选项，就不能再把它计成小问，否则会虚增难度先验。
    circled_as_option = False
    try:
        scanned = analyze_options(raw)
        circled_count = len({ch for ch in _CIRCLED_NUMBERS if ch in raw})
        circled_as_option = bool(scanned.get("marker")) and circled_count >= 4
    except Exception:
        circled_as_option = False
    for match in _SUBQUESTION_PATTERN.finditer(raw):
        token = match.group(1)
        if token.isdigit():
            found.add(int(token))
        elif token in _CIRCLED_TO_INT:
            if circled_as_option:
                continue
            found.add(_CIRCLED_TO_INT[token])
        elif token in _CN_NUMBERS:
            found.add(_CN_NUMBERS[token])
    if not circled_as_option:
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

    explicit_ai = ai_type in (QUESTION_TYPE_SINGLE_CHOICE, QUESTION_TYPE_MULTI_CHOICE)

    # 单选 / 多选是只有模型能做的语义判断，规则层原则上不插手。但有一种情形例外：
    # 题干里白纸黑字写了「多选题」「有多项符合题目要求」「全部选对得满分」这类提示语，
    # 这是规则层手里唯一的硬证据。此时模型若判单选，必须纠正——否则规则层就退化成
    # 模型的传声筒，错题会带着 needs_review=False 直接入库，老师完全看不到。
    multi_conflict = bool(
        multi
        and ai_type == QUESTION_TYPE_SINGLE_CHOICE
        and options["has_options"]
        and structure != QUESTION_FORM_FILL_IN_BLANK
    )
    if multi_conflict:
        evidence["type_conflict"] = "multi_signal_vs_single_choice"
        return QUESTION_TYPE_MULTI_CHOICE, "corrected", evidence

    # 1. Structural macros are the strongest evidence -- but they can only
    # prove "this question has options", never whether one or several are
    # correct.  When the model already answered that, keep its answer: the
    # rule layer must not overwrite a semantic judgement it cannot make.
    if structure == QUESTION_FORM_CHOICE:
        if explicit_ai:
            # 单选/多选这一层完全是模型的判断，来源必须标 ai，不能标 structure，
            # 否则界面会告诉老师「这是结构判定的」，与事实不符。
            return ai_type, "ai", evidence
        kind, _ = choice_kind("structure")
        return kind, "structure", evidence
    if structure == QUESTION_FORM_FILL_IN_BLANK:
        return QUESTION_TYPE_FILL_IN_BLANK, "structure", evidence

    # 2. + 3. Normalized AI value, corrected by local evidence.
    if explicit_ai:
        if options["has_options"]:
            # 模型已明确区分单选/多选，直接采信；仅当模型只给了粗粒度 choice
            # 时才用「多选提示语」兜底判断。
            return ai_type, "ai", evidence
        if blanks:
            return QUESTION_TYPE_FILL_IN_BLANK, "corrected", evidence
        # 题干既没有选项也没有填空位，模型却说是选择题 —— 按解答题处理。
        # （曾尝试「保留模型判断只提示」，但实测让规则层纠回数从 112/162 掉到 70/162，
        #   净损 42 道本可纠对的题，故维持纠正；overridden 会给出复核提示，不会静默。）
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
        signals.append(f"共 {subquestions} 个小问，数量偏多")
    elif subquestions == 2:
        score += 1
        signals.append("共 2 个小问")

    param_discussion = detect_parameter_discussion(raw)
    if param_discussion:
        score += 2
        signals.append("含参讨论或存在性探究")

    hard_words = [word for word in _HARD_WORDS if word in raw]
    if hard_words:
        score += 1
        signals.append("设问含「" + "、".join(hard_words) + "」")

    medium_words = [word for word in _MEDIUM_WORDS if word in raw]
    if medium_words:
        score += 1
        signals.append("题干出现「" + "、".join(medium_words) + "」")

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
    # 注意：裸词「集合」已移出术语表（「求 a 的取值集合」会被误判到必修一第一章），
    # 改由下方 _CONTEXT_HINTS 用带上下文的正则识别真正的集合运算。
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
    "平均数": ("B2-C9-S2",), "中位数": ("B2-C9-S2",), "众数": ("B2-C9-S2",),
    # 以下四个是「只属于必修二《统计》」的纯统计词（标准差/极差/离散程度/离差平方和），
    # 与多义术语「方差」不同，它们在选择性必修三里没有对应节点，可放心单值声明。
    # 它们的作用之一是给「方差」做语境消歧帮手：一道统计题即使只说了一句「求标准差」，
    # 也能把票投到 B2-C9-S2，抵掉下方 X3-C7-S3-P2 因节点名就叫「方差」而从 _auto_terms()
    # 拿到的额外加分。
    "标准差": ("B2-C9-S2",), "极差": ("B2-C9-S2",),
    "离散程度": ("B2-C9-S2",), "离差平方和": ("B2-C9-S2",),
    # 注意：多义术语「方差」不在本行声明。它同时属于必修二《统计》(B2-C9-S2) 与选择性必修三
    # 《随机变量及其分布》(X3-C7-S3-P2)。此前在下方 X3-C7 段落又写了一处同名字典键，
    # Python 后定义覆盖先定义，导致 B2-C9-S2 这条信号被静默丢弃。现已统一改为在下方
    # X3-C7 段落以多值元组一次性声明（见该处注释），请勿在此重复添加同名字典键。
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
    # 「方差」是跨册多义术语：必修二《统计》用它描述样本数据的离散程度，
    # 选择性必修三《随机变量及其分布》用它描述随机变量的数字特征。此处以多值形式同时声明
    # 两个节点码，由消费端的加权打分 + 上下文词做累积消歧：统计题多伴「样本/频率分布/
    # 平均数/中位数/标准差」，概率题多伴「分布列/期望/随机变量/二项分布」。切勿再拆成两处同名字典键，
    # 那会让后写的那个静默覆盖先写的。
    "数学期望": ("X3-C7-S3-P1",), "期望": ("X3-C7-S3-P1",), "方差": ("B2-C9-S2", "X3-C7-S3-P2"),
    "二项分布": ("X3-C7-S4-P1",), "超几何分布": ("X3-C7-S4-P2",), "正态分布": ("X3-C7-S5",),
    "线性回归": ("X3-C8-S2",), "回归": ("X3-C8-S2",), "列联表": ("X3-C8-S3",), "独立性检验": ("X3-C8-S3",),

    # ---- 补充：覆盖此前完全没有显式术语的节/小节节点 ----
    "充分必要": ("B1-C1-S4",), "充要": ("B1-C1-S4-P2",),
    "量词": ("B1-C1-S5",),
    "函数值": ("B1-C3-S1-P1",), "分段函数": ("B1-C3-S4",),
    "指数": ("B1-C4",), "根式": ("B1-C4-S1",),
    "指数函数的概念": ("B1-C4-S2-P1",), "指数函数的图象": ("B1-C4-S2-P2",),
    "常用对数": ("B1-C4-S3-P1",), "反函数": ("B1-C4-S4-P1",),
    "对数函数的图象": ("B1-C4-S4-P2",), "二分法": ("B1-C4-S5",),
    "简谐运动": ("B1-C5-S7",),
    "样本空间": ("B2-C10",), "频率": ("B2-C10-S3",),
    "有向线段": ("B2-C6-S1",), "单位向量": ("B2-C6-S1",),
    "辐角": ("B2-C7-S3",), "三角形式": ("B2-C7-S3",),
    "空间几何体": ("B2-C8",), "抽样": ("B2-C9",), "统计分析": ("B2-C9-S3",),
    "空间向量的线性运算": ("X1-C1-S1-P1",), "空间向量基本定理": ("X1-C1-S2",),
    "空间向量运算的坐标表示": ("X1-C1-S3-P2",), "法向量": ("X1-C1-S4",),
    "直线方程": ("X1-C2",), "两条直线平行": ("X1-C2-S1-P2",),
    "两点式": ("X1-C2-S2-P2",), "交点坐标": ("X1-C2-S3",),
    "两条直线的交点": ("X1-C2-S3-P1",), "两点间的距离": ("X1-C2-S3-P2",),
    "圆与圆的位置关系": ("X1-C2-S5",),
    "椭圆的标准方程": ("X1-C3-S1-P1",), "双曲线的标准方程": ("X1-C3-S2-P1",),
    "抛物线的标准方程": ("X1-C3-S3-P1",), "焦点弦": ("X1-C3-S3-P2",),
    "平均变化率": ("X2-C5-S1-P1",), "瞬时速度": ("X2-C5-S1-P2",),
    "导数公式": ("X2-C5-S2-P1",), "导数的四则运算": ("X2-C5-S2-P2",),
    "排列与组合": ("X3-C6-S2",), "排列数": ("X3-C6-S2-P2",), "组合数": ("X3-C6-S2-P4",),
    "随机变量": ("X3-C7",), "离散型随机变量的数字特征": ("X3-C7-S3",),
    "成对数据": ("X3-C8",), "散点图": ("X3-C8-S1",), "相关关系": ("X3-C8-S1",),
}

#: Surface notation -> chapter hints.  OCR / Word exports often lose the
#: Chinese topic words entirely (``f(x)=a^{x}`` never says 指数函数), so a
#: small notation table keeps the retriever from returning nothing.
_SURFACE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # 只认底数为 a/b 的指数式（教材里指数函数写作 y=a^x），避免 e^{x}、2^{x} 误命中
    (r"(?<![a-zA-Z])[ab]\s*\^\s*\{?\s*x\s*\}?", ("B1-C4-S2",)),
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
    # 裸的「函数」「f(x)」几乎出现在每一道题里，用它定位会淹没真正考点，
    # 因此只在出现必修三专属问法（定义域/值域/单调性/奇偶性/解析式）时才生效。
    (r"(?:定义域|值域|对应关系|单调性|奇偶性|解析式)", ("B1-C3",)),
)

#: 需要上下文才成立的术语：裸词过于泛化，直接匹配会误判。
_CONTEXT_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (r"集合\s*[A-Z]|∁\s*[Uu]|[∩∪⊆⊇∁]|补集|交集|并集|子集|全集|空集|元素",
     ("B1-C1", "B1-C1-S1", "B1-C1-S3")),
)

#: 这些词在教材里出现频率极高但指向性极弱，不作为定位依据。
_TERM_STOPWORDS = frozenset({"集合", "函数", "方程", "图象", "图像", "性质", "应用"})

_LEVEL_WEIGHT = {"book": 0.6, "chapter": 1.0, "section": 1.2, "subsection": 1.3}

#: 面向老师的中文标签，直接渲染到弹窗上，不要改成英文枚举。
_TYPE_LABELS = {
    "single_choice": "单选题",
    "multi_choice": "多选题",
    "fill_in_blank": "填空题",
    "detailed_answer": "解答题",
    "unknown": "未判定",
}
_LEVEL_LABELS = {"easy": "基础题", "medium": "中档题", "hard": "难题"}


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
            if len(piece) >= 2 and piece not in _TERM_STOPWORDS:
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
    for pattern, codes in _CONTEXT_HINTS:
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

    # 规则层一旦覆盖（或纠正）模型给出的题型，必须让老师看见，绝不能静默改写。
    ai_expected = normalize_ai_question_type(
        payload.get("question_type", payload.get("question_form"))
    )
    overridden = bool(
        ai_expected
        and ai_expected != question_type
        and type_source in ("structure", "corrected", "rule")
    )
    # 判为选择题、但模型没有明确区分单选/多选、且题干也没有多选提示语时，
    # 单选/多选无法可靠区分，必须交老师确认。
    ai_raw = str(payload.get("question_type", payload.get("question_form")) or "").strip().lower()
    ai_explicit = ai_raw in {"single_choice", "multi_choice", "单选题", "多选题", "单选", "多选"}
    ambiguous_stem = detect_ambiguous_choice_stem(raw)
    choice_ambiguous = bool(
        question_type in (QUESTION_TYPE_SINGLE_CHOICE, QUESTION_TYPE_MULTI_CHOICE)
        and not decision["multi_signals"]
        and (
            # 模型只给了粗粒度 choice，单选/多选本来就没定
            not ai_explicit
            # 模型说是多选，但题干找不到任何多选提示语 —— 对不上，要老师看一眼
            or ai_expected == QUESTION_TYPE_MULTI_CHOICE
            # 模型说是单选，但题干是「下列…正确的是」式设问 —— 这句式单多选都用，必须看一眼
            or (ai_expected == QUESTION_TYPE_SINGLE_CHOICE and ambiguous_stem)
        )
    )

    review_reasons: list[str] = []
    type_conflict = decision.get("type_conflict")
    if type_conflict == "multi_signal_vs_single_choice":
        signals = [s for s in (decision.get("multi_signals") or []) if s]
        # signals 里已经自带「」，不要再套一层，否则会显示成「出现「…」」
        hint = "、".join(signals[:2]) if signals else "出现多选提示语"
        review_reasons.append(f"题干{hint}，与模型判定的单选题冲突，已按多选题处理，请核对")
    elif overridden:
        review_reasons.append(
            f"规则层依据题干结构把模型的「{_TYPE_LABELS.get(ai_expected, ai_expected)}」"
            f"判定为「{_TYPE_LABELS.get(question_type, question_type)}」，请核对"
        )
    if choice_ambiguous:
        review_reasons.append(
            "题干是「下列…正确的是」式设问，单选题和多选都可能，请人工确认"
            if ambiguous_stem
            else "题干未出现多选提示语，单选题/多选题需人工确认"
        )
    if question_type == QUESTION_TYPE_UNKNOWN:
        review_reasons.append("题型无法可靠判定，请手动选择")
    if difficulty_conflict:
        review_reasons.append(
            f"难度判定存在分歧：模型判为{_LEVEL_LABELS.get(difficulty, difficulty)}，"
            f"规则先验为{_LEVEL_LABELS.get(prior, prior)}，请人工确认"
        )
    if difficulty_source == "prior":
        review_reasons.append("难度未能由模型给出，取规则先验值，请人工确认")
    if chapter_source == "rule":
        review_reasons.append("教材章节由规则检索得出，非模型判断，请核对")
    if chapter_source == "fallback" or not chapter_code:
        review_reasons.append("未能匹配到教材章节，请手动选择")

    # 只要列得出具体理由就要复核；反过来，列不出理由就不该打扰老师。
    # 这样前端永远拿得到「为什么要我看这一条」的答案，不会白屏。
    needs_review = bool(review_reasons)

    ranked_candidates = _rank_type_candidates(
        question_type, options["has_options"], decision["blanks"], bool(decision["multi_signals"])
    )

    # ``is_fallback`` means "the whole payload is degraded", not "something is
    # uncertain": it is reserved for the case where neither the question type
    # nor the chapter could be determined at all.  Softer cases (rule-derived
    # chapter, difficulty conflict, corrected type) only raise
    # ``needs_review`` so the UI never shows two overlapping warnings.
    is_fallback = question_type == QUESTION_TYPE_UNKNOWN and chapter_source == "fallback"

    # 粗粒度 form 的来源要单独算：只要结构层独立定出的 form 与最终 form 一致，
    # 功劳就归结构层，与细粒度题型（单选/多选）的来源无关 —— 后者常常是模型给的，
    # 两者混为一谈会让界面把模型的判断说成「结构标记」。
    structure_form = str(decision.get("structure") or "")
    form_source = "structure" if structure_form and structure_form == question_form else type_source

    return {
        "question_type": question_type,
        "question_type_source": type_source,
        "question_type_candidates": ranked_candidates,
        "question_form": question_form,
        "question_form_source": form_source,
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
        "review_reasons": review_reasons,
        "needs_review": needs_review,
        "is_fallback": is_fallback,
    }


def _rank_type_candidates(
    decided: str, has_options: bool, blanks: bool, multi: bool
) -> list[str]:
    """Ranked plausible question types, decided one first.

    When the engine could not decide (``unknown``) this list is what the UI
    offers the teacher as one-click alternatives instead of making them pick
    from the full four-value set.
    """

    if has_options:
        ranked = ["multi_choice", "single_choice"] if multi else ["single_choice", "multi_choice"]
    elif blanks:
        ranked = ["fill_in_blank", "detailed_answer"]
    else:
        ranked = ["detailed_answer", "fill_in_blank", "single_choice"]

    if decided in (QUESTION_TYPE_SINGLE_CHOICE, QUESTION_TYPE_MULTI_CHOICE,
                   QUESTION_TYPE_FILL_IN_BLANK, QUESTION_TYPE_DETAILED_ANSWER):
        return [decided, *[item for item in ranked if item != decided][:2]]
    return ranked[:3]
