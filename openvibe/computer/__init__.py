"""Computer Use — screen capture, mouse, keyboard, and app control.

Public API
----------
- :class:`ComputerSandbox` — session-scoped sandbox with audit log
- :func:`get_sandbox` — retrieve (or create) the sandbox for a session
- :func:`clear_sandbox` — discard a session's sandbox
- :func:`create_computer_use_registry` — build a ToolRegistry with all CU tools
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
