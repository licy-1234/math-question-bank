"""Standardize vector arrow notation in question Markdown.

The rewrite is purely textual: only vector-shaped macro spans change, so
prose, code fences, TikZ pictures and ``\\text{...}`` arguments stay
byte-for-byte identical.

Conventions (Chinese textbook style):

* a bare single symbol keeps the compact accent — ``\\vec{v}``;
* everything else gets the stretched arrow — ``\\overrightarrow{AB}``;
  a single-letter base carrying sub/superscripts counts as "everything else"
  too — ``\\overrightarrow{v_{1}}``;
* ``\\vv`` (physics package), ``\\overset{\\rightarrow}{AB}``, the Unicode
  combining arrows and the spaced form ``\\vec {AB}`` collapse onto those two
  canonical shapes;
* scripts written outside the group (``\\vec{v}_{1}``) are pulled inside
  (``\\overrightarrow{v_{1}}``) so both spellings converge — except a
  superscript, which belongs to the whole expression and stays outside:
  ``\\vec{AB}^T`` → ``\\overrightarrow{AB}^T`` (a transpose, not an arrow
  over ``AB^T``). Scripts the author already braced are never moved.

Recognized shapes — one macro, one balanced brace group, whose body is a single
base symbol (a run of ASCII letters/digits, or one control sequence such as
``\\alpha``) optionally followed by ``_``/``^`` groups::

    \\vec{v}        \\vec{v_{1}}      \\vec{v_{1}^{(2)}}   \\vec{v}_{1}
    \\vec{AB}_{1}   \\vec{P_{1}}      \\vec{v_{i}^{2}}     \\vec{\\alpha_{1}}

Anything else is deliberately left untouched because the ownership of the
script would be a guess, and a wrong guess silently changes the math:
``\\vec{v_{1}+1}`` (a sum, not a subscripted vector), the brace-less
``\\vec v``, the ambiguous ``\\vec{v}_{12}``, and
``\\overset{\\rightarrow}{v_{1}}`` (base itself braced) all keep their
original bytes.

Because the function is idempotent, it is safe to run on already-normalized
content (imports, edits and re-saves all pass through it).
"""

from __future__ import annotations

import re

__all__ = ["normalize_vector_notation"]


_PLACEHOLDER = "\ue000{}\ue001"
_PLACEHOLDER_RE = re.compile("\ue000(\\d+)\ue001")

# Regions whose bytes must survive untouched: fenced code, inline code spans,
# raw HTML literals, TikZ/verbatim bodies and content-lock references.
_PROTECTED = re.compile(
    r"(?P<fence>(?m:^[ \t]{0,3}(?P<fence_mark>`{3,}|~{3,})[^\n]*\n)"
    r"[\s\S]*?(?:(?m:^[ \t]{0,3}(?P=fence_mark)[ \t]*(?:\n|$))|\Z))"
    r"|`+[^`\n]*`+"
    r"|(?is:<(?P<html_tag>code|pre|mathbank-math)\b[^>]*>[\s\S]*?</(?P=html_tag)\s*>)"
    r"|<!--[\s\S]*?(?:-->|\Z)"
    r"|\\begin\{(?P<environment>tikzpicture|pgfpicture|axis|verbatim\*?|"
    r"Verbatim|lstlisting|minted|filecontents\*?)\}[\s\S]*?"
    r"(?:\\end\{(?P=environment)\}|\Z)"
    r"|\[\[\s*MBM_[A-Za-z0-9_:\-]+\s*\]\]"
    r"|!?\[[^\]\n]*\]\([^\n)]*\)"
)

# Macro arguments that are literal text, not math: their body is never a vector.
_TEXT_LIKE = re.compile(
    r"\\(?:text|textrm|textsf|texttt|textnormal|textbf|textit|mbox|hbox"
    r"|operatorname|label|tag|url|path|cite|ref|eqref)\s*\{[^{}\n]*\}",
    re.IGNORECASE,
)

_VV = re.compile(r"\\vv(?![A-Za-z])")

_RIGHT_ARROW_NAMES = (
    "longrightarrow|Longrightarrow|rightarrow|to|rightharpoonup|rightharpoondown"
)
_LEFT_ARROW_NAMES = (
    "longleftarrow|Longleftarrow|leftarrow|gets|leftharpoonup|hookleftarrow"
)
_OVERSET_RIGHT = re.compile(
    r"\\overset\s*\{\s*\\(?:" + _RIGHT_ARROW_NAMES + r")\s*\}"
    r"\s*\{\s*([^{}\n]*?)\s*\}"
)
_OVERSET_LEFT = re.compile(
    r"\\overset\s*\{\s*\\(?:" + _LEFT_ARROW_NAMES + r")\s*\}"
    r"\s*\{\s*([^{}\n]*?)\s*\}"
)

# OCR/AI output occasionally keeps the Unicode combining arrow after a letter.
_COMBINING_ARROW = re.compile(r"([A-Za-z])[\u20D0\u20D1\u20D6\u20D7](?![A-Za-z])")

# A braced vector macro; ``\s*`` absorbs the spaced form (``\vec {AB}``).
_VECTOR_COMMAND = re.compile(r"\\(vec|overrightarrow|overleftarrow)(?![A-Za-z])\s*\{")

# The base of a vector: a run of ASCII letters/digits, or one control sequence.
_BASE = re.compile(r"(?:[A-Za-z0-9]+|\\[A-Za-z]+)")
# One unbraced script atom (``_i``, ``_\alpha``, ``_1``).
_SCRIPT_TOKEN = re.compile(r"\\[A-Za-z]+|[A-Za-z0-9]")

_SINGLE_SYMBOL_COMMAND = re.compile(r"\\[A-Za-z]+")


def _is_single_symbol(base: str) -> bool:
    if len(base) == 1:
        return True
    return _SINGLE_SYMBOL_COMMAND.fullmatch(base) is not None


def _protect(text: str) -> tuple[str, list[str]]:
    """Swap protected regions for placeholders; return the saved literals."""

    spans: list[str] = []

    def save(match: re.Match[str]) -> str:
        spans.append(match.group(0))
        return _PLACEHOLDER.format(len(spans) - 1)

    protected = _PROTECTED.sub(save, text)
    protected = _TEXT_LIKE.sub(save, protected)
    return protected, spans


def _restore(text: str, spans: list[str]) -> str:
    return _PLACEHOLDER_RE.sub(lambda m: spans[int(m.group(1))], text)


def _matching_brace(text: str, open_index: int) -> int | None:
    """Index of the brace closing ``text[open_index]``; ``None`` when unbalanced."""

    depth = 0
    cursor = open_index
    length = len(text)
    while cursor < length:
        char = text[cursor]
        if char == "\\":
            cursor += 2
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cursor
        cursor += 1
    return None


def _parse_scripts(
    text: str, index: int, *, allow_superscript: bool = True
) -> tuple[str | None, int]:
    """Inline ``_``/``^`` groups starting at ``index`` as braced scripts.

    ``allow_superscript=False`` ends the run at a ``^`` group instead of
    absorbing it: a superscript written *after* the closing brace belongs to
    the whole expression (``\\vec{AB}^T`` is a transpose, not an arrow over
    ``AB^T``), so it has to stay outside the arrow. Subscripts still merge,
    because ``\\vec{v}_{1}`` and ``\\vec{v_{1}}`` are the same vector.
    """

    pieces: list[str] = []
    cursor = index
    length = len(text)
    while cursor < length and text[cursor] in "_^":
        mark = text[cursor]
        if mark == "^" and not allow_superscript:
            break
        following = cursor + 1
        if following < length and text[following] == "{":
            close = _matching_brace(text, following)
            if close is None:
                return None, index
            pieces.append(mark + "{" + text[following + 1:close] + "}")
            cursor = close + 1
            continue
        token = _SCRIPT_TOKEN.match(text, following)
        if token is None:
            return None, index
        pieces.append(mark + "{" + token.group(0) + "}")
        cursor = token.end()
        # ``\vec{v}_{12}``: the script is longer than one atom, so where it
        # belongs is ambiguous. Leave the whole span alone instead of guessing.
        if cursor < length and text[cursor].isalnum():
            return None, index
    return "".join(pieces), cursor


def _split_base(core: str) -> tuple[str, str] | None:
    """Split ``core`` into (base, script groups); ``None`` when it is not simple."""

    match = _BASE.match(core)
    if match is None:
        return None
    base = match.group(0)
    rest = core[match.end():]
    scripts: list[str] = []
    cursor = 0
    while cursor < len(rest):
        if rest[cursor] not in "_^":
            return None
        parsed, cursor = _parse_scripts(rest, cursor)
        if parsed is None:
            return None
        scripts.append(parsed)
    return base, "".join(scripts)


def _canonical_vector(
    name: str, body: str, symbol_count: int, *, has_script: bool
) -> str:
    if name == "overleftarrow":
        return "\\overleftarrow{%s}" % body
    if name == "overrightarrow":
        return "\\overrightarrow{%s}" % body
    if symbol_count == 1 and not has_script:
        return "\\vec{%s}" % body
    return "\\overrightarrow{%s}" % body


def _rewrite_braced_vectors(text: str) -> str:
    """Rewrite every well-formed ``\\vec``-family macro in a single pass."""

    pieces: list[str] = []
    cursor = 0
    while True:
        match = _VECTOR_COMMAND.search(text, cursor)
        if match is None:
            pieces.append(text[cursor:])
            return "".join(pieces)
        pieces.append(text[cursor:match.start()])
        open_index = match.end() - 1
        close_index = _matching_brace(text, open_index)
        if close_index is None:
            pieces.append(text[match.start():match.end()])
            cursor = match.end()
            continue
        after = close_index + 1
        core = text[open_index + 1:close_index].strip()
        split = _split_base(core) if core else None
        if split is None:
            pieces.append(text[match.start():after])
            cursor = after
            continue
        base, inner_scripts = split
        trailing, end_index = _parse_scripts(text, after, allow_superscript=False)
        if trailing is None:
            pieces.append(text[match.start():after])
            cursor = after
            continue
        symbol_count = 1 if base.startswith("\\") else len(base)
        body = base + inner_scripts + trailing
        pieces.append(
            _canonical_vector(
                match.group(1),
                body,
                symbol_count,
                has_script=bool(inner_scripts or trailing),
            )
        )
        cursor = end_index


def _vector(base: str, *, left: bool = False) -> str:
    if left:
        return "\\overleftarrow{%s}" % base
    if _is_single_symbol(base):
        return "\\vec{%s}" % base
    return "\\overrightarrow{%s}" % base


def _rewrite_overset(match: re.Match[str], *, left: bool) -> str:
    base = match.group(1)
    if not base:
        return match.group(0)
    return _vector(base, left=left)


def normalize_vector_notation(text: str) -> str:
    """Return ``text`` with every vector arrow written in canonical form."""

    if not isinstance(text, str):
        return ""
    if not text:
        return text

    protected, spans = _protect(text)
    out = _VV.sub(lambda _match: "\\vec", protected)
    out = _OVERSET_RIGHT.sub(lambda m: _rewrite_overset(m, left=False), out)
    out = _OVERSET_LEFT.sub(lambda m: _rewrite_overset(m, left=True), out)
    out = _COMBINING_ARROW.sub(lambda m: "\\vec{%s}" % m.group(1), out)
    out = _rewrite_braced_vectors(out)
    return _restore(out, spans)
