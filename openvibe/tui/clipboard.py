"""Shared clipboard helper for the TUI."""

from __future__ import annotations

import contextlib
import subprocess
import sys


def copy_to_clipboard(text: str) -> bool:
    """Copy *text* to the system clipboard.  Returns True on success."""
    with contextlib.suppress(Exception):
        if sys.platform == "darwin":
            subprocess.run(["pbcopy"], input=text.encode(), check=True, timeout=2)
            return True
        for cmd in (
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
            ["wl-copy"],
        ):
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=2)
                return True
            except (FileNotFoundError, subprocess.SubprocessError):
                continue
    return False


def strip_markup(markup: str) -> str:
    """Return plain text with all Rich markup tags removed."""
    from rich.text import Text

    try:
        return Text.from_markup(markup).plain
    except Exception:
        # Fallback: naive tag stripper
        import re
        return re.sub(r"\[/?[^\[\]]*\]", "", markup)
