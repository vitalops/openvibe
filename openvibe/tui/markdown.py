"""Markdown-to-Rich-markup renderer.

Converts markdown text into a Rich ``Text`` renderable so that content can be
rendered inside a plain ``Static`` widget — which, unlike Rich's ``Markdown``
renderable, preserves terminal text selection.

Syntax highlighting for fenced code blocks is handled by Pygments (ships with
Rich).
"""

from __future__ import annotations

import re

import mistune
from pygments import highlight as _pygments_highlight
from pygments.formatters import TerminalTrueColorFormatter
from pygments.lexers import get_lexer_by_name, guess_lexer
from pygments.util import ClassNotFound
from rich.console import ConsoleOptions, Group, RenderableType, RenderResult
from rich.markup import escape
from rich.measure import Measurement
from rich.style import Style
from rich.text import Text

_CODE_BG = Style(bgcolor="#282828")


# ---------------------------------------------------------------------------
# Pygments → ANSI highlighted string
# ---------------------------------------------------------------------------

_FORMATTER = TerminalTrueColorFormatter(style="monokai")


def _highlight_code(code: str, lang: str | None) -> str:
    """Return *code* with ANSI colour escapes via Pygments."""
    try:
        lexer = get_lexer_by_name(lang) if lang else guess_lexer(code)
    except ClassNotFound:
        lexer = get_lexer_by_name("text")
    return _pygments_highlight(code, lexer, _FORMATTER).rstrip("\n")


# ---------------------------------------------------------------------------
# Sentinels for code blocks (ANSI content that must not be parsed as markup)
# ---------------------------------------------------------------------------

_CODE_START = "\x00CS\x00"
_CODE_END = "\x00CE\x00"
_CODE_RE = re.compile(re.escape(_CODE_START) + "(.*?)" + re.escape(_CODE_END), re.DOTALL)

# ---------------------------------------------------------------------------
# Mistune renderer → Rich markup (with ANSI sentinels for code)
# ---------------------------------------------------------------------------

_LI = "\x00LI\x00"
_INDENT = "  "


class _RichRenderer(mistune.HTMLRenderer):
    """Produce Rich console markup from markdown tokens.

    Extends ``HTMLRenderer`` to inherit its ``render_token`` which extracts
    children/attrs into the v2-style ``(text, **attrs)`` call signatures.
    """

    NAME = "rich"

    # -- inline ------------------------------------------------------------

    def text(self, text: str) -> str:
        return escape(text)

    def emphasis(self, text: str) -> str:
        return f"[italic]{text}[/italic]"

    def strong(self, text: str) -> str:
        return f"[bold]{text}[/bold]"

    def codespan(self, text: str) -> str:
        return f"[bold cyan]{escape(text)}[/bold cyan]"

    def linebreak(self) -> str:
        return "\n"

    def softbreak(self) -> str:
        return "\n"

    def link(self, text: str, url: str, title: str | None = None) -> str:
        return f"[link={url}]{text}[/link] [dim]({escape(url)})[/dim]"

    def image(self, text: str, url: str, title: str | None = None) -> str:
        label = text or title or url
        return f"[dim](image: {escape(label)})[/dim]"

    def inline_html(self, html: str) -> str:
        return escape(html)

    # -- block -------------------------------------------------------------

    def paragraph(self, text: str) -> str:
        return f"{text}\n"

    def heading(self, text: str, level: int, **attrs: object) -> str:
        if level == 1:
            return f"[bold underline]{text}[/bold underline]\n"
        if level == 2:
            return f"[bold]{text}[/bold]\n"
        return f"[bold dim]{text}[/bold dim]\n"

    def blank_line(self) -> str:
        return "\n"

    def thematic_break(self) -> str:
        return "[dim]───────────────────────────────────[/dim]\n"

    def block_code(self, code: str, info: str | None = None, **attrs: object) -> str:
        lang = info.split()[0] if info else None
        highlighted = _highlight_code(code.rstrip("\n"), lang)
        # Use ANSI dim for the ``` bars so the entire block lives inside the
        # sentinel and gets a uniform background applied in render_markdown.
        dim_on, dim_off = "\x1b[2m", "\x1b[22m"
        bar = f"{dim_on}```{lang or ''}{dim_off}"
        end_bar = f"{dim_on}```{dim_off}"
        return f"{_CODE_START}{bar}\n{highlighted}\n{end_bar}{_CODE_END}\n"

    def block_quote(self, text: str) -> str:
        lines = text.rstrip("\n").split("\n")
        quoted = "\n".join(f"[dim]│[/dim] {line}" for line in lines)
        return f"{quoted}\n"

    def block_html(self, html: str) -> str:
        return f"{escape(html)}\n"

    def block_text(self, text: str) -> str:
        return text

    def block_error(self, text: str) -> str:
        return f"[red]{escape(text)}[/red]\n"

    def list(self, text: str, ordered: bool, **attrs: object) -> str:
        items = text.split(_LI)
        items = [item for item in items if item.strip()]
        lines: list[str] = []
        for i, item in enumerate(items, start=attrs.get("start", 1)):  # type: ignore[arg-type]
            item_lines = item.rstrip("\n").split("\n")
            if ordered:
                prefix = f"[dim]{i}.[/dim] "
            else:
                prefix = "[dim]•[/dim] "
            lines.append(f"{prefix}{item_lines[0]}")
            for cont in item_lines[1:]:
                lines.append(f"{_INDENT}{cont}")
        return "\n".join(lines) + "\n"

    def list_item(self, text: str) -> str:
        return f"{_LI}{text}"


# ---------------------------------------------------------------------------
# Code block renderable (pads background to full width)
# ---------------------------------------------------------------------------


class _CodeBlock:
    """Renderable that draws syntax-highlighted code with a full-width background."""

    def __init__(self, ansi_content: str) -> None:
        self._text = Text.from_ansi(ansi_content)

    def __rich_console__(self, console: object, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        for line in self._text.split():
            line.set_length(width)
            line.stylize(_CODE_BG)
            yield line

    def __rich_measure__(self, console: object, options: ConsoleOptions) -> Measurement:
        return Measurement(1, options.max_width)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_md = mistune.create_markdown(renderer=_RichRenderer())

_TRAILING_NL = re.compile(r"\n$")


def render_markdown(text: str) -> RenderableType:
    """Convert *text* (markdown) to a Rich renderable.

    Rich markup sections are parsed via ``Text.from_markup``; code blocks
    containing raw ANSI escapes become ``_CodeBlock`` renderables with a
    full-width background.  Everything is combined into a ``Group``.
    """
    raw = _md(text)
    raw = _TRAILING_NL.sub("", raw)

    parts: list[RenderableType] = []
    pos = 0
    for m in _CODE_RE.finditer(raw):
        before = raw[pos : m.start()]
        if before:
            parts.append(Text.from_markup(before))
        parts.append(_CodeBlock(m.group(1)))
        pos = m.end()
    tail = raw[pos:]
    if tail:
        parts.append(Text.from_markup(tail))

    if len(parts) == 1:
        return parts[0]
    return Group(*parts)
