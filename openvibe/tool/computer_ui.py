"""UITool — accessibility-tree based UI interaction, cross-platform.

Clicks buttons, types text, navigates menus, and reads element values through
each platform's native accessibility API.  No pixel coordinates needed —
elements are found by their title, role, or position in the tree.

Platform backends
-----------------
macOS   — AppleScript / System Events (built-in, no extra deps)
Linux   — AT-SPI2 via ``pyatspi`` (preferred) with ``xdotool`` fallbacks
Windows — UI Automation via ``pywinauto`` (preferred) with Win32 fallbacks

Why this beats coordinate-based mouse clicks
--------------------------------------------
* No Retina / HiDPI scaling translation needed.
* Works at any window position or screen resolution.
* Errors are descriptive: "button 'Save' not found" vs a silent mis-click.
* Uses the same accessibility layer as screen readers.

Graceful degradation
--------------------
If the preferred library (pyatspi / pywinauto) is missing, the tool falls
back to subprocess tools (xdotool on Linux, basic Win32 commands on Windows)
and surfaces a clear install hint in any error message.

For apps that expose no accessibility elements (Electron games, custom
renderers) the tool returns a clear message and the caller should fall back
to screenshot + mouse with image_width/image_height for coordinate scaling.
"""

from __future__ import annotations

import platform
import subprocess
import time
from typing import Any, Literal

from pydantic import Field

from openvibe.tool.base import Tool, ToolContext, ToolResult

_PLATFORM = platform.system()  # "Darwin" | "Linux" | "Windows"


# ---------------------------------------------------------------------------
# Tool definition (platform-agnostic interface)
# ---------------------------------------------------------------------------


class UITool(Tool):
    """Interact with desktop UI elements by name — no pixel coordinates needed.

    Prefer this over the mouse tool whenever the target element has a visible
    label or title.  Use mouse clicks only for unlabelled canvas areas
    (games, drawing tools, video players) where no accessible elements exist.
    """

    name = "ui"
    description = (
        "Interact with UI elements by name on any OS — no coordinates needed. "
        "ALWAYS try this before the mouse tool. Actions:\n"
        "  get_tree   — list all accessible elements in the app window\n"
        "  click      — click a button or element by its title\n"
        "  click_menu — click a menu item, e.g. File → Save\n"
        "  type       — type text (clipboard-paste, Unicode-safe)\n"
        "  press_key  — press a key or chord: key='return', modifiers=['command']\n"
        "  get_value  — read the current text value of a named element"
    )

    class Params(Tool.Params):
        action: Literal["get_tree", "click", "click_menu", "type", "press_key", "get_value"] = Field(
            description="UI action to perform."
        )
        app: str = Field(
            description=(
                "Application name / process name. "
                "macOS: process name as in Activity Monitor, e.g. 'TextEdit', 'Safari'. "
                "Linux: process name or window title substring, e.g. 'gedit', 'firefox'. "
                "Windows: executable name or window title, e.g. 'Notepad', 'notepad.exe'."
            )
        )
        title: str | None = Field(
            default=None,
            description="Title or label of the UI element (for click, get_value).",
        )
        role: str | None = Field(
            default=None,
            description=(
                "Element role to narrow the search. "
                "macOS: 'button', 'text field', 'text area', 'checkbox', 'pop up button'. "
                "Linux: 'push button', 'entry', 'text', 'check box', 'combo box'. "
                "Windows: 'Button', 'Edit', 'Text', 'CheckBox', 'ComboBox', 'ListItem'. "
                "Optional — omit to search all roles."
            ),
        )
        text: str | None = Field(
            default=None,
            description="Text to type. Required for action='type'.",
        )
        menu: str | None = Field(
            default=None,
            description="Menu bar menu name for click_menu, e.g. 'File', 'Edit', 'View'.",
        )
        menu_item: str | None = Field(
            default=None,
            description="Menu item name for click_menu, e.g. 'Save', 'Copy', 'Undo'.",
        )
        key: str | None = Field(
            default=None,
            description=(
                "Key for press_key: 'return', 'escape', 'tab', 'space', 'delete', "
                "'up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', "
                "or a single character like 's', 'z'."
            ),
        )
        modifiers: list[str] = Field(
            default_factory=list,
            description=(
                "Modifier keys for press_key: 'command' (macOS), 'ctrl'/'control', "
                "'shift', 'alt'/'option'. E.g. ['command'] for cmd+key."
            ),
        )
        window_index: int = Field(
            default=1,
            description="Window index (1 = frontmost). Used by get_tree, click, get_value.",
        )

    async def execute(self, ctx: ToolContext, params: "UITool.Params") -> ToolResult:  # type: ignore[override]
        import asyncio
        from openvibe.computer.sandbox import ActionType, get_sandbox

        await ctx.check_permission(
            tool="ui",
            argument=f"{params.action} in {params.app}",
            description=f"UI accessibility action: {params.action} in '{params.app}'",
        )

        sandbox = get_sandbox(ctx.session_id)
        loop = asyncio.get_event_loop()

        try:
            result_msg = await loop.run_in_executor(None, self._do_action, params)
        except (RuntimeError, ValueError, ImportError) as exc:
            await sandbox.record_action(
                ActionType.MOUSE_CLICK,
                params={"action": params.action, "app": params.app, "title": params.title},
                error=str(exc),
            )
            return ToolResult(
                title=f"UI error: {params.action} in {params.app}",
                output=str(exc),
                error=True,
            )
        except Exception as exc:
            return ToolResult(
                title=f"UI error: {params.action}",
                output=str(exc),
                error=True,
            )

        await sandbox.record_action(
            ActionType.MOUSE_CLICK,
            params={"action": params.action, "app": params.app, "title": params.title},
            result=result_msg[:200],
        )
        label = params.title or params.menu_item or params.key or ""
        return ToolResult(
            title=f"UI: {params.action} '{label}' in {params.app}",
            output=result_msg,
        )

    @staticmethod
    def _do_action(params: "UITool.Params") -> str:
        if _PLATFORM == "Darwin":
            return _macos_dispatch(params)
        if _PLATFORM == "Linux":
            return _linux_dispatch(params)
        if _PLATFORM == "Windows":
            return _windows_dispatch(params)
        raise RuntimeError(
            f"UITool: unsupported platform '{_PLATFORM}'. "
            "Use the mouse tool with image_width/image_height instead."
        )


# ===========================================================================
# macOS backend — AppleScript / System Events
# ===========================================================================


def _osascript(script: str, timeout: int = 15) -> str:
    """Run an AppleScript and return stdout.  Raises RuntimeError on failure."""
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip())
    return r.stdout.strip()


def _macos_dispatch(params: "UITool.Params") -> str:
    if params.action == "get_tree":
        return _macos_get_tree(params.app, params.window_index)
    if params.action == "click":
        return _macos_click(params.app, params.title, params.role, params.window_index)
    if params.action == "click_menu":
        return _macos_click_menu(params.app, params.menu, params.menu_item)
    if params.action == "type":
        return _macos_type(params.app, params.text)
    if params.action == "press_key":
        return _macos_press_key(params.app, params.key, params.modifiers)
    if params.action == "get_value":
        return _macos_get_value(params.app, params.title, params.role, params.window_index)
    raise ValueError(f"Unknown action: {params.action!r}")


def _macos_get_tree(app: str, window_index: int = 1) -> str:
    script = f"""
tell application "System Events"
    tell process "{app}"
        set win to window {window_index}
        set winTitle to ""
        try
            set winTitle to title of win
        end try
        set output to "Window " & {window_index} & ": \\"" & winTitle & "\\"\\n"
        set allElems to entire contents of win
        repeat with elem in allElems
            try
                set r to role of elem
                set t to ""
                try
                    set t to title of elem
                end try
                if t is "" then
                    try
                        set t to value of elem as text
                    on error
                        set t to ""
                    end try
                end if
                if t is "" then
                    try
                        set t to description of elem
                    end try
                end if
                if t is not "" and t is not missing value then
                    set output to output & "  [" & r & "] " & t & "\\n"
                end if
            end try
        end repeat
        return output
    end tell
end tell
"""
    result = _osascript(script)
    if not result.strip():
        return (
            f"No accessible elements found in {app} window {window_index}. "
            "App may use custom rendering (Electron, game engines). "
            "Fall back to mouse tool with image_width/image_height."
        )
    return result


def _macos_click(app: str, title: str | None, role: str | None, window_index: int = 1) -> str:
    if not title and not role:
        raise ValueError("Provide at least 'title' or 'role' for click.")
    if title:
        escaped = title.replace('"', '\\"')
        script = f"""
tell application "System Events"
    tell process "{app}"
        set allElems to entire contents of window {window_index}
        repeat with elem in allElems
            try
                set matched to false
                try
                    if title of elem is "{escaped}" then set matched to true
                end try
                if not matched then
                    try
                        if description of elem is "{escaped}" then set matched to true
                    end try
                end if
                if matched then
                    click elem
                    return "Clicked [" & (role of elem) & "] \\"{escaped}\\" in {app}."
                end if
            end try
        end repeat
        error "No element titled \\"{escaped}\\" in {app} window {window_index}. Run get_tree to list elements."
    end tell
end tell
"""
    else:
        script = f"""
tell application "System Events"
    tell process "{app}"
        click first {role} of window {window_index}
        return "Clicked first [{role}] in {app}."
    end tell
end tell
"""
    return _osascript(script)


def _macos_click_menu(app: str, menu: str | None, menu_item: str | None) -> str:
    if not menu or not menu_item:
        raise ValueError("Both 'menu' and 'menu_item' are required for click_menu.")
    em = menu.replace('"', '\\"')
    ei = menu_item.replace('"', '\\"')
    script = f"""
tell application "System Events"
    tell process "{app}"
        click menu item "{ei}" of menu "{em}" of menu bar item "{em}" of menu bar 1
        return "Clicked {menu} → {menu_item} in {app}."
    end tell
end tell
"""
    return _osascript(script)


def _macos_type(app: str, text: str | None) -> str:
    if not text:
        raise ValueError("'text' is required for action='type'.")
    _osascript(f'tell application "{app}" to activate')
    time.sleep(0.3)
    subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True)
    time.sleep(0.1)
    _osascript('tell application "System Events" to keystroke "v" using {command down}')
    time.sleep(0.2)
    preview = text[:60] + ("…" if len(text) > 60 else "")
    return f"Typed {len(text)} chars into {app}: {preview!r}"


_MACOS_KEY_MAP: dict[str, tuple[str, bool]] = {
    "return": ("return", False), "enter": ("return", False),
    "escape": ("escape", False), "esc": ("escape", False),
    "tab": ("tab", False), "space": ("space", False),
    "delete": ("delete", False), "backspace": ("delete", False),
    "up": ("126", True), "down": ("125", True),
    "left": ("123", True), "right": ("124", True),
    "home": ("115", True), "end": ("119", True),
    "pageup": ("116", True), "pagedown": ("121", True),
    "f1": ("122", True), "f2": ("120", True), "f3": ("99", True),
    "f4": ("118", True), "f5": ("96", True), "f6": ("97", True),
    "f7": ("98", True), "f8": ("100", True), "f9": ("101", True),
    "f10": ("109", True), "f11": ("103", True), "f12": ("111", True),
}
_MACOS_MOD_MAP = {
    "command": "command down", "cmd": "command down",
    "shift": "shift down", "option": "option down",
    "alt": "option down", "control": "control down", "ctrl": "control down",
}


def _macos_press_key(app: str, key: str | None, modifiers: list[str]) -> str:
    if not key:
        raise ValueError("'key' is required for action='press_key'.")
    _osascript(f'tell application "{app}" to activate')
    time.sleep(0.15)
    mod_parts = [_MACOS_MOD_MAP.get(m.lower(), f"{m} down") for m in modifiers]
    mod_str = "{" + ", ".join(mod_parts) + "}" if mod_parts else ""
    kl = key.lower()
    if kl in _MACOS_KEY_MAP:
        val, is_code = _MACOS_KEY_MAP[kl]
        verb = "key code" if is_code else "keystroke"
        script = (
            f'tell application "System Events" to {verb} {val} using {mod_str}'
            if mod_str else
            f'tell application "System Events" to {verb} {val}'
        )
    else:
        esc = key.replace('"', '\\"')
        script = (
            f'tell application "System Events" to keystroke "{esc}" using {mod_str}'
            if mod_str else
            f'tell application "System Events" to keystroke "{esc}"'
        )
    _osascript(script)
    combo = ("+".join(modifiers) + "+" if modifiers else "") + key
    return f"Pressed {combo} in {app}."


def _macos_get_value(app: str, title: str | None, role: str | None, window_index: int = 1) -> str:
    if title:
        esc = title.replace('"', '\\"')
        script = f"""
tell application "System Events"
    tell process "{app}"
        set allElems to entire contents of window {window_index}
        repeat with elem in allElems
            try
                if title of elem is "{esc}" then
                    return value of elem as text
                end if
            end try
        end repeat
        error "No element titled \\"{esc}\\" found."
    end tell
end tell
"""
    elif role:
        script = f"""
tell application "System Events"
    tell process "{app}"
        return value of first {role} of window {window_index} as text
    end tell
end tell
"""
    else:
        raise ValueError("Provide 'title' or 'role' for get_value.")
    return _osascript(script)


# ===========================================================================
# Linux backend — AT-SPI2 (pyatspi) preferred, xdotool fallback
# ===========================================================================


def _xdotool(*args: str, check: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["xdotool", *args], capture_output=True, text=True, timeout=10, check=check
    )


def _has_xdotool() -> bool:
    return subprocess.run(["which", "xdotool"], capture_output=True).returncode == 0


def _linux_dispatch(params: "UITool.Params") -> str:
    try:
        from openvibe.computer.deps import ensure_import
        ensure_import("pyatspi")
        return _linux_atspi_dispatch(params)
    except (ImportError, RuntimeError):
        return _linux_xdotool_dispatch(params)


# ---- AT-SPI (pyatspi) path ----

def _atspi_desktop():
    import pyatspi
    return pyatspi.Registry.getDesktop(0)


def _atspi_find_app(name: str):
    """Find an AT-SPI application node by name (case-insensitive, partial match)."""
    import pyatspi
    desktop = pyatspi.Registry.getDesktop(0)
    name_lower = name.lower()
    # Exact match first
    for app in desktop:
        if app and app.name and app.name.lower() == name_lower:
            return app
    # Partial match
    for app in desktop:
        if app and app.name and name_lower in app.name.lower():
            return app
    available = [a.name for a in desktop if a and a.name]
    raise RuntimeError(
        f"App '{name}' not found in AT-SPI tree. "
        f"Running apps: {', '.join(available) or 'none'}. "
        "Make sure the app is open and AT-SPI accessibility is enabled "
        "(export GTK_MODULES=gail:atk-bridge on older GTK apps)."
    )


def _atspi_walk(node: Any, depth: int = 0, max_depth: int = 10):
    """Yield all AT-SPI nodes up to max_depth."""
    if depth > max_depth:
        return
    yield node
    try:
        for i in range(node.childCount):
            try:
                yield from _atspi_walk(node.getChildAtIndex(i), depth + 1, max_depth)
            except Exception:
                pass
    except Exception:
        pass


def _atspi_find(app_node: Any, title: str | None, role: str | None) -> Any:
    """Find first AT-SPI element matching title and/or role."""
    title_lower = title.lower() if title else None
    role_lower = role.lower() if role else None

    for node in _atspi_walk(app_node):
        try:
            name = (node.name or "").lower()
            node_role = (node.getLocalizedRoleName() or "").lower()

            name_ok = not title_lower or title_lower in name
            role_ok = not role_lower or role_lower in node_role

            if name_ok and role_ok and (title_lower or role_lower):
                if node.name or node.getLocalizedRoleName():
                    return node
        except Exception:
            pass
    return None


def _linux_atspi_dispatch(params: "UITool.Params") -> str:
    if params.action == "get_tree":
        return _linux_atspi_get_tree(params.app, params.window_index)
    if params.action == "click":
        return _linux_atspi_click(params.app, params.title, params.role)
    if params.action == "click_menu":
        return _linux_atspi_click_menu(params.app, params.menu, params.menu_item)
    if params.action == "type":
        return _linux_type(params.app, params.text)
    if params.action == "press_key":
        return _linux_press_key(params.app, params.key, params.modifiers)
    if params.action == "get_value":
        return _linux_atspi_get_value(params.app, params.title, params.role)
    raise ValueError(f"Unknown action: {params.action!r}")


def _linux_atspi_get_tree(app_name: str, window_index: int = 1) -> str:
    app = _atspi_find_app(app_name)
    lines: list[str] = [f"App: {app.name}"]
    seen = 0
    for node in _atspi_walk(app, max_depth=6):
        if seen >= 150:
            lines.append("  … (truncated — app has many elements)")
            break
        try:
            name = node.name or ""
            role = node.getLocalizedRoleName() or ""
            if name or role:
                lines.append(f"  [{role}] {name}")
                seen += 1
        except Exception:
            pass
    if len(lines) == 1:
        return (
            f"No accessible elements found in '{app_name}'. "
            "App may use custom rendering. Fall back to mouse tool."
        )
    return "\n".join(lines)


def _linux_atspi_click(app_name: str, title: str | None, role: str | None) -> str:
    import pyatspi
    app = _atspi_find_app(app_name)
    node = _atspi_find(app, title, role)
    if node is None:
        raise RuntimeError(
            f"No element title='{title}' role='{role}' in '{app_name}'. "
            "Run get_tree to see available elements."
        )
    # Try AT-SPI action first (click/press/activate)
    try:
        action = node.queryAction()
        action_names = [action.getName(i).lower() for i in range(action.nActions)]
        for pref in ("click", "press", "activate", "toggle"):
            if pref in action_names:
                action.doAction(action_names.index(pref))
                return f"Clicked [{node.getLocalizedRoleName()}] '{node.name}' in {app_name}."
        # Any action
        if action.nActions > 0:
            action.doAction(0)
            return f"Activated [{node.getLocalizedRoleName()}] '{node.name}' in {app_name}."
    except Exception:
        pass
    # Fall back: move mouse to element centre and click
    try:
        bbox = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        cx, cy = bbox.x + bbox.width // 2, bbox.y + bbox.height // 2
        _xdotool("mousemove", "--sync", str(cx), str(cy))
        _xdotool("click", "1")
        return f"Clicked at ({cx},{cy}) [{node.getLocalizedRoleName()}] '{node.name}' in {app_name}."
    except Exception as exc:
        raise RuntimeError(f"Could not click element '{title}': {exc}") from exc


def _linux_atspi_click_menu(app_name: str, menu: str | None, menu_item: str | None) -> str:
    import pyatspi
    if not menu or not menu_item:
        raise ValueError("Both 'menu' and 'menu_item' are required.")
    app = _atspi_find_app(app_name)
    # Find menu bar → menu → item
    menu_bar = _atspi_find(app, None, "menu bar")
    if menu_bar is None:
        menu_bar = _atspi_find(app, None, "menubar")
    if menu_bar is None:
        raise RuntimeError(f"No menu bar found in '{app_name}'.")
    menu_node = _atspi_find(menu_bar, menu, "menu")
    if menu_node is None:
        raise RuntimeError(f"Menu '{menu}' not found in '{app_name}'.")
    # Open the menu
    try:
        act = menu_node.queryAction()
        act.doAction(0)
        time.sleep(0.2)
    except Exception:
        pass
    item_node = _atspi_find(menu_node, menu_item, "menu item")
    if item_node is None:
        raise RuntimeError(f"Menu item '{menu_item}' not found under '{menu}' in '{app_name}'.")
    try:
        act = item_node.queryAction()
        act.doAction(0)
    except Exception:
        bbox = item_node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
        cx, cy = bbox.x + bbox.width // 2, bbox.y + bbox.height // 2
        _xdotool("mousemove", "--sync", str(cx), str(cy))
        _xdotool("click", "1")
    return f"Clicked {menu} → {menu_item} in {app_name}."


def _linux_atspi_get_value(app_name: str, title: str | None, role: str | None) -> str:
    app = _atspi_find_app(app_name)
    node = _atspi_find(app, title, role)
    if node is None:
        raise RuntimeError(f"No element title='{title}' role='{role}' in '{app_name}'.")
    try:
        val = node.queryValue()
        return str(val.currentValue)
    except Exception:
        pass
    try:
        text = node.queryText()
        return text.getText(0, -1)
    except Exception:
        pass
    return node.name or "(no value)"


# ---- xdotool-only fallback for Linux ----

def _linux_xdotool_dispatch(params: "UITool.Params") -> str:
    """Fallback when pyatspi is not installed — limited but usable."""
    if params.action == "type":
        return _linux_type(params.app, params.text)
    if params.action == "press_key":
        return _linux_press_key(params.app, params.key, params.modifiers)
    if params.action in ("get_tree", "click", "click_menu", "get_value"):
        if not _has_xdotool():
            raise ImportError(
                "pyatspi is required for accessibility-based UI on Linux: "
                "pip install pyatspi  (or: sudo apt install python3-pyatspi)\n"
                "xdotool also not found. Install it for basic input: "
                "sudo apt install xdotool"
            )
        raise ImportError(
            f"action='{params.action}' requires pyatspi for element discovery. "
            "Install it: pip install pyatspi  (or: sudo apt install python3-pyatspi)\n"
            "For 'type' and 'press_key', xdotool is sufficient and already available."
        )
    raise ValueError(f"Unknown action: {params.action!r}")


# ---- Shared Linux helpers ----

def _linux_focus_window(app_name: str) -> None:
    """Best-effort: focus the window matching app_name."""
    r = _xdotool("search", "--name", app_name, "windowactivate", "--sync")
    if r.returncode != 0:
        _xdotool("search", "--class", app_name, "windowactivate", "--sync")
    time.sleep(0.2)


def _linux_type(app_name: str, text: str | None) -> str:
    if not text:
        raise ValueError("'text' is required for action='type'.")
    _linux_focus_window(app_name)
    # Try clipboard paste (Unicode-safe)
    for clipboard_tool in [
        (["xclip", "-selection", "clipboard"], ["xdotool", "key", "--clearmodifiers", "ctrl+v"]),
        (["xsel", "--clipboard", "--input"],    ["xdotool", "key", "--clearmodifiers", "ctrl+v"]),
    ]:
        copy_cmd, paste_cmd = clipboard_tool
        r = subprocess.run(copy_cmd, input=text.encode("utf-8"),
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            time.sleep(0.1)
            subprocess.run(paste_cmd, check=True, timeout=5)
            time.sleep(0.1)
            preview = text[:60] + ("…" if len(text) > 60 else "")
            return f"Typed {len(text)} chars into {app_name}: {preview!r}"
    # Last resort: xdotool type (ASCII only, slow)
    if _has_xdotool():
        _xdotool("type", "--clearmodifiers", "--delay", "20", text, check=True)
        return f"Typed (xdotool, ASCII mode) {len(text)} chars into {app_name}."
    raise RuntimeError(
        "Could not type text: install xclip or xsel for Unicode support: "
        "sudo apt install xclip"
    )


_LINUX_KEY_MAP = {
    "return": "Return", "enter": "Return",
    "escape": "Escape", "esc": "Escape",
    "tab": "Tab", "space": "space",
    "delete": "Delete", "backspace": "BackSpace",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "home": "Home", "end": "End",
    "pageup": "Prior", "pagedown": "Next",
    **{f"f{i}": f"F{i}" for i in range(1, 13)},
}
_LINUX_MOD_MAP = {
    "command": "ctrl",  # remap macOS cmd → Ctrl on Linux
    "cmd": "ctrl",
    "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift",
    "alt": "alt", "option": "alt",
}


def _linux_press_key(app_name: str, key: str | None, modifiers: list[str]) -> str:
    if not key:
        raise ValueError("'key' is required for action='press_key'.")
    if not _has_xdotool():
        raise RuntimeError("xdotool is required for press_key on Linux: sudo apt install xdotool")
    _linux_focus_window(app_name)
    mapped_key = _LINUX_KEY_MAP.get(key.lower(), key)
    mapped_mods = [_LINUX_MOD_MAP.get(m.lower(), m) for m in modifiers]
    combo = "+".join(mapped_mods + [mapped_key]) if mapped_mods else mapped_key
    _xdotool("key", "--clearmodifiers", combo, check=True)
    display = ("+".join(modifiers) + "+" if modifiers else "") + key
    return f"Pressed {display} in {app_name}."


# ===========================================================================
# Windows backend — pywinauto (UI Automation) preferred, fallbacks where needed
# ===========================================================================

# Role name translation: user-friendly → pywinauto control_type
_WIN_ROLE_MAP = {
    "button": "Button", "btn": "Button",
    "edit": "Edit", "text field": "Edit", "input": "Edit",
    "text": "Text", "label": "Text", "static": "Text",
    "checkbox": "CheckBox", "check box": "CheckBox",
    "combobox": "ComboBox", "combo box": "ComboBox", "dropdown": "ComboBox",
    "listitem": "ListItem", "list item": "ListItem",
    "listbox": "List", "list": "List",
    "tab": "TabItem", "tab item": "TabItem",
    "tree item": "TreeItem", "treeitem": "TreeItem",
    "menu item": "MenuItem", "menuitem": "MenuItem",
    "toolbar": "ToolBar", "tool bar": "ToolBar",
}


def _win_connect(app_name: str):
    """Connect to a running Windows application via pywinauto."""
    from openvibe.computer.deps import ensure_import
    _pywinauto = ensure_import("pywinauto")
    Application = _pywinauto.Application  # type: ignore[attr-defined]

    errors: list[str] = []
    # Try executable name
    for backend in ("uia", "win32"):
        for kwargs in (
            {"path": app_name},
            {"title_re": f".*{app_name}.*"},
            {"title": app_name},
        ):
            try:
                return Application(backend=backend).connect(**kwargs, timeout=3)
            except Exception as e:
                errors.append(str(e))

    raise RuntimeError(
        f"Could not connect to '{app_name}' on Windows. "
        f"Make sure the app is running. Tried: {'; '.join(errors[:3])}"
    )


def _windows_dispatch(params: "UITool.Params") -> str:
    if params.action == "get_tree":
        return _windows_get_tree(params.app, params.window_index)
    if params.action == "click":
        return _windows_click(params.app, params.title, params.role, params.window_index)
    if params.action == "click_menu":
        return _windows_click_menu(params.app, params.menu, params.menu_item)
    if params.action == "type":
        return _windows_type(params.app, params.text)
    if params.action == "press_key":
        return _windows_press_key(params.app, params.key, params.modifiers)
    if params.action == "get_value":
        return _windows_get_value(params.app, params.title, params.role, params.window_index)
    raise ValueError(f"Unknown action: {params.action!r}")


def _windows_get_tree(app_name: str, window_index: int = 1) -> str:
    app = _win_connect(app_name)
    dlg = app.top_window()
    lines = [f"Window: {dlg.window_text()}"]
    seen = 0
    try:
        for ctrl in dlg.descendants():
            if seen >= 150:
                lines.append("  … (truncated)")
                break
            try:
                title = ctrl.window_text().strip()
                role = ctrl.friendly_class_name()
                if title:
                    lines.append(f"  [{role}] {title}")
                    seen += 1
            except Exception:
                pass
    except Exception:
        pass
    if len(lines) == 1:
        return (
            f"No accessible elements in '{app_name}'. "
            "Fall back to mouse tool with coordinates."
        )
    return "\n".join(lines)


def _windows_click(app_name: str, title: str | None, role: str | None, window_index: int = 1) -> str:
    app = _win_connect(app_name)
    dlg = app.top_window()
    win_role = _WIN_ROLE_MAP.get((role or "").lower())
    try:
        if title and win_role:
            ctrl = dlg.child_window(title=title, control_type=win_role)
        elif title:
            ctrl = dlg.child_window(title=title)
        elif win_role:
            ctrl = dlg.child_window(control_type=win_role)
        else:
            raise ValueError("Provide 'title' or 'role' for click.")
        ctrl.click_input()
        return f"Clicked '{title or role}' in {app_name}."
    except Exception as exc:
        raise RuntimeError(
            f"Could not click '{title or role}' in '{app_name}'. "
            f"Run get_tree to see available elements. Error: {exc}"
        ) from exc


def _windows_click_menu(app_name: str, menu: str | None, menu_item: str | None) -> str:
    if not menu or not menu_item:
        raise ValueError("Both 'menu' and 'menu_item' are required for click_menu.")
    app = _win_connect(app_name)
    dlg = app.top_window()
    try:
        dlg.menu_select(f"{menu}->{menu_item}")
        return f"Clicked {menu} → {menu_item} in {app_name}."
    except Exception as exc:
        raise RuntimeError(
            f"Could not click menu '{menu}→{menu_item}' in '{app_name}': {exc}"
        ) from exc


def _windows_type(app_name: str, text: str | None) -> str:
    if not text:
        raise ValueError("'text' is required for action='type'.")
    # Use pyperclip for Unicode-safe clipboard paste (auto-installed if absent)
    from openvibe.computer.deps import ensure_import
    pyperclip = ensure_import("pyperclip")
    pyperclip.copy(text)

    app = _win_connect(app_name)
    app.top_window().type_keys("^v")  # Ctrl+V
    time.sleep(0.15)
    preview = text[:60] + ("…" if len(text) > 60 else "")
    return f"Typed {len(text)} chars into {app_name}: {preview!r}"


_WIN_KEY_MAP = {
    "return": "{ENTER}", "enter": "{ENTER}",
    "escape": "{ESC}", "esc": "{ESC}",
    "tab": "{TAB}", "space": " ",
    "delete": "{DELETE}", "backspace": "{BACKSPACE}",
    "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}",
    "home": "{HOME}", "end": "{END}",
    "pageup": "{PGUP}", "pagedown": "{PGDN}",
    **{f"f{i}": f"{{F{i}}}" for i in range(1, 13)},
}
_WIN_MOD_PREFIX = {
    "command": "^", "cmd": "^",      # remap macOS cmd → Ctrl on Windows
    "ctrl": "^", "control": "^",
    "shift": "+",
    "alt": "%", "option": "%",
}


def _windows_press_key(app_name: str, key: str | None, modifiers: list[str]) -> str:
    if not key:
        raise ValueError("'key' is required for action='press_key'.")
    app = _win_connect(app_name)
    prefix = "".join(_WIN_MOD_PREFIX.get(m.lower(), "") for m in modifiers)
    key_str = _WIN_KEY_MAP.get(key.lower(), key if len(key) == 1 else f"{{{key.upper()}}}")
    app.top_window().type_keys(f"{prefix}{key_str}")
    display = ("+".join(modifiers) + "+" if modifiers else "") + key
    return f"Pressed {display} in {app_name}."


def _windows_get_value(app_name: str, title: str | None, role: str | None, window_index: int = 1) -> str:
    app = _win_connect(app_name)
    dlg = app.top_window()
    win_role = _WIN_ROLE_MAP.get((role or "").lower())
    try:
        if title and win_role:
            ctrl = dlg.child_window(title=title, control_type=win_role)
        elif title:
            ctrl = dlg.child_window(title=title)
        elif win_role:
            ctrl = dlg.child_window(control_type=win_role)
        else:
            raise ValueError("Provide 'title' or 'role' for get_value.")
        return ctrl.window_text()
    except Exception as exc:
        raise RuntimeError(
            f"Could not get value of '{title or role}' in '{app_name}': {exc}"
        ) from exc
