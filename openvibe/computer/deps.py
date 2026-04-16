"""Runtime dependency installer for platform-specific computer-use packages.

openvibe auto-installs the right packages for the current OS the first time a
computer-use tool needs them, so users never have to specify extras manually.

Usage::

    from openvibe.computer.deps import ensure_import

    pyatspi = ensure_import("pyatspi")           # Linux AT-SPI2
    pywinauto = ensure_import("pywinauto")        # Windows UI Automation
    pyperclip = ensure_import("pyperclip")        # Windows clipboard

``ensure_import`` returns the imported module on success.  It raises
``RuntimeError`` with an actionable message if installation fails (e.g. no pip,
no network, or root-only environment).
"""

from __future__ import annotations

import importlib
import importlib.util
import subprocess
import sys
from typing import Any

# pip_name may differ from the import name (e.g. "Pillow" → import "PIL")
_IMPORT_TO_PIP: dict[str, str] = {
    "pyatspi": "pyatspi",
    "pywinauto": "pywinauto",
    "pyperclip": "pyperclip",
    "PIL": "Pillow",
    "mss": "mss",
    "pyautogui": "pyautogui",
}


def _pip_install(pip_name: str) -> None:
    """Run ``pip install <pip_name>`` in the current interpreter."""
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", pip_name],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Auto-install of '{pip_name}' failed.\n"
            f"stdout: {result.stdout.strip()}\n"
            f"stderr: {result.stderr.strip()}\n\n"
            f"Run manually:  pip install {pip_name}"
        )


def ensure_import(import_name: str, pip_name: str | None = None) -> Any:
    """Import *import_name*, installing via pip first if absent.

    Parameters
    ----------
    import_name:
        The Python import name (``import <import_name>``).
    pip_name:
        The PyPI package name to install.  Defaults to ``import_name`` (or a
        built-in mapping if one exists).

    Returns
    -------
    The imported module.

    Raises
    ------
    RuntimeError
        If the package cannot be installed automatically.
    """
    resolved_pip = pip_name or _IMPORT_TO_PIP.get(import_name, import_name)

    if importlib.util.find_spec(import_name) is None:
        _pip_install(resolved_pip)
        # Invalidate the import caches so the freshly installed package is found
        importlib.invalidate_caches()

    try:
        return importlib.import_module(import_name)
    except ImportError as exc:
        raise RuntimeError(
            f"Package '{resolved_pip}' was installed but '{import_name}' still cannot "
            f"be imported. You may need to restart openvibe.\n"
            f"Original error: {exc}"
        ) from exc
