"""Computer Use — screen capture, mouse, keyboard, app control, OCR, clipboard.

Public API
----------
- :class:`ComputerSandbox` — session-scoped sandbox with audit log
- :func:`get_sandbox` — retrieve (or create) the sandbox for a session
- :func:`clear_sandbox` — discard a session's sandbox

SOTA enhancements
-----------------
- :mod:`openvibe.computer.marks` — Set-of-Marks (SoM) overlay + cursor dot
- :mod:`openvibe.computer.ocr` — screen-region text extraction
- :mod:`openvibe.tool.computer_clipboard` — ClipboardTool (read/write)
- :mod:`openvibe.tool.computer_ocr` — OCRTool
- ScreenshotTool: ``marks``, ``show_cursor``, ``zoom`` params
- MouseTool: ``middle_click``, ``triple_click``, ``left_down``, ``left_up``,
  ``cursor_position``, directional ``scroll``
- KeyboardTool: ``hold`` action
"""

from openvibe.computer.sandbox import (
    ActionType,
    AuditEntry,
    ComputerSandbox,
    clear_sandbox,
    get_sandbox,
)

__all__ = [
    "ActionType",
    "AuditEntry",
    "ComputerSandbox",
    "get_sandbox",
    "clear_sandbox",
]
