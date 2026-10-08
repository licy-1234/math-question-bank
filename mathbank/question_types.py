"""Pure helpers for question form detection and paper type grouping."""

import re


PAPER_TYPE_ORDER = ("single_choice", "multi_choice", "fill_in_blank", "detailed_answer")


def normalize_section_order(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item for item in value if isinstance(item, str) and item))


def paper_type_order(present_types, paper_type: str, question_types=None, section_order=None) -> list[str]:
    """Use saved section order, appending any types that were added afterwards."""
    order = list(PAPER_TYPE_ORDER)
    if paper_type == "exam_19":
        return order
    present = list(dict.fromkeys(present_types))
    for item in question_types or []:
        value = item.get("value") if isinstance(item, dict) else None
        if isinstance(value, str) and value in present and value not in order:
            order.append(value)
    order.extend(value for value in present if value not in order)
    preferred = normalize_section_order(section_order)
    return preferred + [value for value in order if value not in preferred]


def custom_type_labels(question_types=None) -> dict[str, str]:
    """Custom labels are plain text; each output format must escape them itself."""
    labels = {}
    for item in question_types or []:
        if not isinstance(item, dict):
            continue
        value, label = item.get("value"), item.get("label")
        if isinstance(value, str) and isinstance(label, str) and label.strip():
            labels.setdefault(value, label)
    return labels


def is_written_question_type(q_type: str) -> bool:
    return q_type not in ("single_choice", "multi_choice", "fill_in_blank")


QUESTION_FORM_CHOICE = "choice"
QUESTION_FORM_FILL_IN_BLANK = "fill_in_blank"
QUESTION_FORM_DETAILED_ANSWER = "detailed_answer"
QUESTION_FORM_UNKNOWN = "unknown"

# Four-value question types.  ``single_choice`` / ``multi_choice`` are kept
# apart here; the coarse ``*_FORM_*`` constants above stay for storage and
# for callers (paper export, Word export) that only need three states.
QUESTION_TYPE_SINGLE_CHOICE = "single_choice"
QUESTION_TYPE_MULTI_CHOICE = "multi_choice"
QUESTION_TYPE_FILL_IN_BLANK = "fill_in_blank"
QUESTION_TYPE_DETAILED_ANSWER = "detailed_answer"
QUESTION_TYPE_UNKNOWN = "unknown"


_AI_TYPE_MAPPING = {
    "single_choice": QUESTION_TYPE_SINGLE_CHOICE,
    "single-choice": QUESTION_TYPE_SINGLE_CHOICE,
    "singlechoice": QUESTION_TYPE_SINGLE_CHOICE,
    "单选题": QUESTION_TYPE_SINGLE_CHOICE,
    "单选": QUESTION_TYPE_SINGLE_CHOICE,
    "multi_choice": QUESTION_TYPE_MULTI_CHOICE,
    "multi-choice": QUESTION_TYPE_MULTI_CHOICE,
    "multichoice": QUESTION_TYPE_MULTI_CHOICE,
    "多选题": QUESTION_TYPE_MULTI_CHOICE,
    "多选": QUESTION_TYPE_MULTI_CHOICE,
    "多项选择题": QUESTION_TYPE_MULTI_CHOICE,
    "fill_in_blank": QUESTION_TYPE_FILL_IN_BLANK,
    "fill-in-blank": QUESTION_TYPE_FILL_IN_BLANK,
    "填空题": QUESTION_TYPE_FILL_IN_BLANK,
    "填空": QUESTION_TYPE_FILL_IN_BLANK,
    "detailed_answer": QUESTION_TYPE_DETAILED_ANSWER,
    "解答题": QUESTION_TYPE_DETAILED_ANSWER,
    "解答": QUESTION_TYPE_DETAILED_ANSWER,
    "unknown": QUESTION_TYPE_UNKNOWN,
    "未知": QUESTION_TYPE_UNKNOWN,
}


_CHOICES_ENV_PATTERN = re.compile(
    r"\\begin\s*\{\s*choices\s*\}",
    re.IGNORECASE,
)
_FILLIN_PATTERN = re.compile(r"\\fillin\b", re.IGNORECASE)


def detect_structured_question_form(content: str) -> str | None:
    """Return a definitive form only when normalized LaTeX structure proves it.

    A choices environment takes precedence because a choice option can itself
    contain a blank-like expression without changing the enclosing question
    into a fill-in-the-blank item.

    Besides the two native macros the function now also recognises the
    structural shapes that OCR / Word exports produce: a run of A/B/C/D
    option markers, ``①②③④`` / ``甲乙丙丁`` markers, the choice macros
    ``\\choice`` / ``\\fourchoices`` / ``\\begin{tasks}``, and on the blank
    side ``\\underline{...}`` placeholders, ``____`` runs and bracket blanks.
    """

    normalized = str(content or "")
    if _CHOICES_ENV_PATTERN.search(normalized):
        return QUESTION_FORM_CHOICE
    if _FILLIN_PATTERN.search(normalized):
        return QUESTION_FORM_FILL_IN_BLANK

    # The richer evidence lives in ``classify_rules`` (which imports this
    # module).  Importing it lazily keeps the module-level import graph
    # one-directional while still routing every caller through one
    # implementation.
    from mathbank.classify_rules import structured_form_from_evidence  # noqa: PLC0415

    return structured_form_from_evidence(normalized)


def normalize_ai_question_type(value: object) -> str | None:
    """Map model output to one of the four question types.

    Returns ``None`` when the model only produced a coarse ``choice`` /
    ``选择题`` (or nothing usable at all) so that the caller resolves
    single-vs-multi and choice-vs-blank from the rule evidence instead.
    """

    normalized = str(value or "").strip().lower()
    return _AI_TYPE_MAPPING.get(normalized)


def normalize_ai_question_form(value: object) -> str:
    """Collapse model output to a coarse form that cannot encode single/multi."""

    normalized = str(value or "").strip().lower()
    mapping = {
        "choice": QUESTION_FORM_CHOICE,
        "choice_question": QUESTION_FORM_CHOICE,
        "single_choice": QUESTION_FORM_CHOICE,
        "multi_choice": QUESTION_FORM_CHOICE,
        "选择题": QUESTION_FORM_CHOICE,
        "单选题": QUESTION_FORM_CHOICE,
        "多选题": QUESTION_FORM_CHOICE,
        "fill_in_blank": QUESTION_FORM_FILL_IN_BLANK,
        "fill-in-blank": QUESTION_FORM_FILL_IN_BLANK,
        "填空题": QUESTION_FORM_FILL_IN_BLANK,
        "detailed_answer": QUESTION_FORM_DETAILED_ANSWER,
        "解答题": QUESTION_FORM_DETAILED_ANSWER,
        "unknown": QUESTION_FORM_UNKNOWN,
        "未知": QUESTION_FORM_UNKNOWN,
    }
    return mapping.get(normalized, QUESTION_FORM_UNKNOWN)


_OPTION_LETTER_PATTERN = re.compile(
    r"[A-D]\s*[.、)．:：）]\s*\S",
)


def detect_choice_options(content: str) -> bool:
    """Return True when content shows explicit A/B/C/D choice options.

    Used to correct a common AI misclassification where a written-answer
    question (解答题, no options) is wrongly tagged as ``choice``.

    The old single regex was replaced by the evidence based scanner in
    ``mathbank.classify_rules.analyze_options``: the previous pattern
    ``[A-D]\\s*[.、)．:：）]\\s*\\S`` matched anywhere in the stem, so
    ``角A、B、C``, ``集合A、B``, ``抛物线C：y²`` and ``∁_U(A\\cup B)=`` were
    all treated as option markers.  The new scanner enforces a leading
    context (line start / whitespace / explicit bracket), rejects
    function-call context such as ``P(A)``, and requires at least two
    distinct A-D letters (or a recognised option macro).
    """

    from mathbank.classify_rules import analyze_options  # noqa: PLC0415

    return analyze_options(content)["has_options"]
