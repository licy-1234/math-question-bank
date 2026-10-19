"""Standardize vector arrow notation in question Markdown.

The rewrite is purely textual: only vector-shaped macro spans change, so
prose, code fences, TikZ pictures and ``\\text{...}`` arguments stay
byte-for-byte identical.

Conventions (Chinese textbook style):

* multi-symbol vectors render with a stretched arrow — ``\\overrightarrow{AB}``;
* single-symbol vectors keep the compact accent — ``\\vec{v}``;
* ``\\vv`` (physics package), ``\\overset{\\rightarrow}{AB}``, the Unicode
  combining arrows and the spaced form ``\\vec {AB}`` all collapse onto the
  two canonical shapes above.

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

# Braced vector macros. ``\s*`` absorbs the spaced form (``\vec {AB}``) and the
# inner ``\s*`` drops the padding (``\vec{ AB }``). Nested braces do not match,
# so ``\vec{v_{1}}`` and similar constructs are left alone.
_BRACED_VECTOR = re.compile(
    r"\\(vec|overrightarrow|overleftarrow)\s*\{\s*([^{}\n]*?)\s*\}"
)

# A base that is a single character or a single control sequence.
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


def _rewrite_braced(match: re.Match[str]) -> str:
    name, base = match.group(1), match.group(2)
    if not base:
        return match.group(0)
    if name == "overleftarrow":
        return "\\overleftarrow{%s}" % base
    if name == "overrightarrow":
        return "\\overrightarrow{%s}" % base
    return _vector(base)


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
    out = _BRACED_VECTOR.sub(_rewrite_braced, out)
    return _restore(out, spans)
