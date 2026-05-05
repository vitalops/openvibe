"""Set-of-Marks (SoM) visual prompting and cursor overlay.

References
----------
* Yang et al. 2023 "Set-of-Mark Prompting Unleashes Extraordinary Visual
  Grounding in GPT-4V" — https://arxiv.org/abs/2310.11441
* Anthropic computer-use-demo — cursor position overlay pattern
* Microsoft OmniParser — numbered bounding-box labeling on screenshots
* AgentS (simular-ai) — accessibility tree + visual grounding fusion

How it works
------------
1. ``get_interactive_elements()`` queries the platform accessibility API
   (AppleScript / AT-SPI2 / UI Automation) for the frontmost window's
   interactive controls and returns their logical-pixel bounding boxes.
2. ``draw_som_marks()`` paints numbered semi-transparent chips on a PIL
   Image.  Accessibility coords are in *logical* screen pixels; the caller
   passes ``scale_x/y`` to map correctly onto downscaled screenshots
   (e.g. Retina Mac: mss captures at 2× then we resize to ≤1920 px wide,
   so scale_x = screenshot_w / pyautogui_logical_w ≈ 1.333).
3. ``overlay_cursor()`` draws a red ring + white dot at the current cursor
   position, matching the visual style used in Anthropic's computer-use demo.

Usage::

    from openvibe.computer.marks import get_interactive_elements, draw_som_marks, overlay_cursor
    import pyautogui

    elements = get_interactive_elements()          # from frontmost app
    cx, cy = pyautogui.position()                  # logical cursor coords
    # scale_x = screenshot_width / logical_screen_width
    img, mark_map, summary = draw_som_marks(pil_img, elements, scale_x, scale_y)
    img = overlay_cursor(img, cx, cy, scale_x, scale_y)
"""

from __future__ import annotations

import platform
import subprocess
from typing import Any

_PLATFORM = platform.system()

# ---------------------------------------------------------------------------
# Colour palette — 10 high-contrast colours that cycle by mark index
# ---------------------------------------------------------------------------

_COLORS: list[tuple[int, int, int]] = [
    (239, 68,  68),   # red-500
    (59,  130, 246),  # blue-500
    (34,  197, 94),   # green-500
    (251, 146, 60),   # orange-400
    (168, 85,  247),  # purple-500
    (236, 72,  153),  # pink-500
    (20,  184, 166),  # teal-500
    (234, 179, 8),    # yellow-500
    (14,  165, 233),  # sky-500
    (132, 204, 22),   # lime-500
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_interactive_elements(app_name: str | None = None) -> list[dict[str, Any]]:
    """Return interactive UI elements with logical-pixel bounding boxes.

    Each dict contains: ``{mark, role, label, x, y, w, h}``
    where ``x, y, w, h`` are in *logical* screen pixels (same space as
    ``pyautogui.position()`` / ``pyautogui.size()``).

    Never raises — returns an empty list on any failure.
    """
    try:
        if _PLATFORM == "Darwin":
            return _macos_get_elements(app_name)
        if _PLATFORM == "Linux":
            return _linux_get_elements(app_name)
        if _PLATFORM == "Windows":
            return _windows_get_elements(app_name)
    except Exception:
        pass
    return []


def draw_som_marks(
    img: Any,  # PIL.Image.Image
    elements: list[dict[str, Any]],
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> tuple[Any, dict[int, dict[str, Any]], str]:
    """Overlay numbered bounding-box chips on *img*.

    Parameters
    ----------
    img:
        PIL Image to annotate (not modified in-place).
    elements:
        Element list from :func:`get_interactive_elements`.
    scale_x, scale_y:
        Ratio ``screenshot_pixels / logical_pixels`` per axis.
        E.g. on a 2× Retina Mac captured then downscaled to 1920 px wide:
        ``scale_x = 1920 / 1440 ≈ 1.333``.

    Returns
    -------
    ``(annotated_image, mark_map, summary_text)``

    * ``annotated_image`` — PIL Image with numbered boxes drawn.
    * ``mark_map`` — ``{mark_number: element_dict}`` for click routing.
    * ``summary_text`` — newline-separated list for the LLM's text context.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore[import-not-found]
    except ImportError:
        return img, {}, ""

    annotated = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    iw, ih = annotated.size

    try:
        font = ImageFont.load_default(size=12)
    except TypeError:
        font = ImageFont.load_default()

    mark_map: dict[int, dict[str, Any]] = {}
    summary_lines: list[str] = []

    for elem in elements:
        mark = elem["mark"]
        ix = int(elem["x"] * scale_x)
        iy = int(elem["y"] * scale_y)
        bw = max(4, int(elem["w"] * scale_x))
        bh = max(4, int(elem["h"] * scale_y))

        # Skip off-screen or zero-size
        if ix >= iw or iy >= ih or ix + bw <= 0 or iy + bh <= 0:
            continue

        r, g, b = _COLORS[mark % len(_COLORS)]

        # Semi-transparent fill
        draw.rectangle([ix, iy, ix + bw, iy + bh], fill=(r, g, b, 40))
        # Solid outline
        draw.rectangle([ix, iy, ix + bw, iy + bh],
                       outline=(r, g, b, 220), width=2)

        # Number chip: top-left of element (flip below if at screen top)
        lx = max(0, ix)
        ly = iy - 19
        if ly < 0:
            ly = iy + bh + 2
        label_text = str(mark)
        chip_w = max(20, len(label_text) * 8 + 8)
        draw.rectangle([lx, ly, lx + chip_w, ly + 17], fill=(r, g, b, 255))
        draw.text((lx + 4, ly + 2), label_text, fill=(255, 255, 255), font=font)

        mark_map[mark] = dict(elem)
        role = elem.get("role", "?")
        label = elem.get("label", "")
        summary_lines.append(
            f"[{mark}] {role} \"{label}\" at ({elem['x']},{elem['y']}) "
            f"{elem['w']}×{elem['h']} — click mark {mark} to interact"
        )

    result = Image.alpha_composite(annotated, overlay).convert("RGB")
    summary = "\n".join(summary_lines) if summary_lines else "(no interactive elements detected)"
    return result, mark_map, summary


def overlay_cursor(
    img: Any,  # PIL.Image.Image
    cursor_x: int,
    cursor_y: int,
    scale_x: float = 1.0,
    scale_y: float = 1.0,
) -> Any:
    """Draw a cursor indicator (red ring + white centre dot) at *cursor_x, cursor_y*.

    Logical-pixel coordinates are scaled to image pixels using *scale_x/y*.
    Matches the visual style used in Anthropic's computer-use reference demo.
    """
    try:
        from PIL import Image, ImageDraw  # type: ignore[import-not-found]
    except ImportError:
        return img

    cx = int(cursor_x * scale_x)
    cy = int(cursor_y * scale_y)
    annotated = img.copy().convert("RGBA")
    overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    R = 10
    # Drop shadow
    draw.ellipse([cx - R - 2, cy - R - 2, cx + R + 2, cy + R + 2],
                 fill=(0, 0, 0, 60))
    # White outer ring
    draw.ellipse([cx - R - 1, cy - R - 1, cx + R + 1, cy + R + 1],
                 outline=(255, 255, 255, 230), width=2)
    # Red filled disc
    draw.ellipse([cx - R, cy - R, cx + R, cy + R],
                 fill=(239, 68, 68, 210))
    # White centre dot
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3],
                 fill=(255, 255, 255, 255))

    return Image.alpha_composite(annotated, overlay).convert("RGB")


# ---------------------------------------------------------------------------
# macOS backend — AppleScript / System Events
# ---------------------------------------------------------------------------

_MACOS_INTERACTIVE_ROLES = (
    "AXButton", "AXTextField", "AXTextArea", "AXCheckBox",
    "AXRadioButton", "AXPopUpButton", "AXComboBox", "AXLink",
    "AXSearchField", "AXMenuButton", "AXDisclosureTriangle",
    "AXSlider", "AXMenuItem",
)


def _macos_get_elements(app_name: str | None = None) -> list[dict[str, Any]]:
    if app_name is None:
        r = subprocess.run(
            ["osascript", "-e",
             "tell application \"System Events\" to get name of first process "
             "whose frontmost is true"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        app_name = r.stdout.strip()

    roles_osa = "{" + ", ".join(f'"{role}"' for role in _MACOS_INTERACTIVE_ROLES) + "}"

    script = f"""
tell application "System Events"
    tell process "{app_name}"
        if (count of windows) = 0 then return ""
        set win to window 1
        set allElems to entire contents of win
        set output to ""
        set elemCount to 0
        repeat with e in allElems
            if elemCount > 120 then exit repeat
            try
                set r to role of e
                if r is in {roles_osa} then
                    set pos to position of e
                    set sz to size of e
                    if (item 1 of sz) > 2 and (item 2 of sz) > 2 then
                        set t to ""
                        try
                            set t to title of e
                        end try
                        if t is "" then
                            try
                                set t to description of e
                            end try
                        end if
                        if t is "" then
                            try
                                set t to value of e as text
                            on error
                                set t to ""
                            end try
                        end if
                        if length of t > 60 then set t to text 1 thru 60 of t
                        set output to output & r & "|" & ¬
                            (item 1 of pos as integer) & "|" & ¬
                            (item 2 of pos as integer) & "|" & ¬
                            (item 1 of sz as integer) & "|" & ¬
                            (item 2 of sz as integer) & "|" & t & "\\n"
                        set elemCount to elemCount + 1
                    end if
                end if
            end try
        end repeat
        return output
    end tell
end tell
"""
    r = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=20,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []

    elements: list[dict[str, Any]] = []
    for i, line in enumerate(r.stdout.strip().splitlines(), start=1):
        parts = line.strip().split("|", 5)
        if len(parts) < 5:
            continue
        try:
            role = parts[0]
            x, y = int(float(parts[1])), int(float(parts[2]))
            w, h = int(float(parts[3])), int(float(parts[4]))
            label = parts[5].strip() if len(parts) > 5 else ""
            elements.append({"mark": i, "role": role, "label": label,
                              "x": x, "y": y, "w": w, "h": h})
        except (ValueError, IndexError):
            continue

    return elements


# ---------------------------------------------------------------------------
# Linux backend — AT-SPI2
# ---------------------------------------------------------------------------

_ATSPI_INTERACTIVE_ROLES: set | None = None


def _get_atspi_roles() -> set:
    global _ATSPI_INTERACTIVE_ROLES
    if _ATSPI_INTERACTIVE_ROLES is None:
        try:
            import pyatspi  # type: ignore[import-not-found]
            _ATSPI_INTERACTIVE_ROLES = {
                pyatspi.ROLE_PUSH_BUTTON, pyatspi.ROLE_TOGGLE_BUTTON,
                pyatspi.ROLE_RADIO_BUTTON, pyatspi.ROLE_CHECK_BOX,
                pyatspi.ROLE_TEXT, pyatspi.ROLE_ENTRY,
                pyatspi.ROLE_PASSWORD_TEXT, pyatspi.ROLE_COMBO_BOX,
                pyatspi.ROLE_LIST_ITEM, pyatspi.ROLE_MENU_ITEM,
                pyatspi.ROLE_LINK, pyatspi.ROLE_SLIDER,
                pyatspi.ROLE_SPIN_BUTTON,
            }
        except ImportError:
            _ATSPI_INTERACTIVE_ROLES = set()
    return _ATSPI_INTERACTIVE_ROLES


def _linux_get_elements(app_name: str | None = None) -> list[dict[str, Any]]:
    try:
        import pyatspi  # type: ignore[import-not-found]
    except ImportError:
        return []

    desktop = pyatspi.Registry.getDesktop(0)
    target_app = None

    if app_name:
        name_lower = app_name.lower()
        for app in desktop:
            if app and app.name and name_lower in app.name.lower():
                target_app = app
                break

    if target_app is None:
        for app in desktop:
            try:
                if app and pyatspi.STATE_ACTIVE in app.getState().getStates():
                    target_app = app
                    break
            except Exception:
                pass

    if target_app is None:
        return []

    interactive = _get_atspi_roles()
    elements: list[dict[str, Any]] = []
    mark = [1]

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 8 or mark[0] > 150:
            return
        try:
            role = node.getRole()
            if role in interactive:
                try:
                    bbox = node.queryComponent().getExtents(pyatspi.DESKTOP_COORDS)
                    if bbox.width >= 4 and bbox.height >= 4:
                        elements.append({
                            "mark": mark[0],
                            "role": node.getLocalizedRoleName() or str(role),
                            "label": (node.name or "").strip()[:60],
                            "x": bbox.x, "y": bbox.y,
                            "w": bbox.width, "h": bbox.height,
                        })
                        mark[0] += 1
                except Exception:
                    pass
            for i in range(node.childCount):
                try:
                    walk(node.getChildAtIndex(i), depth + 1)
                except Exception:
                    pass
        except Exception:
            pass

    walk(target_app)
    return elements


# ---------------------------------------------------------------------------
# Windows backend — pywinauto UI Automation
# ---------------------------------------------------------------------------

_WIN_INTERACTIVE_TYPES = {
    "Button", "CheckBox", "RadioButton", "Edit", "ComboBox",
    "ListItem", "MenuItem", "Hyperlink", "Slider", "Spinner",
    "TabItem", "SplitButton",
}


def _windows_get_elements(app_name: str | None = None) -> list[dict[str, Any]]:
    try:
        import pywinauto  # type: ignore[import-not-found]
    except ImportError:
        return []

    try:
        if app_name:
            app = pywinauto.Application(backend="uia").connect(
                title_re=f".*{app_name}.*", timeout=3
            )
        else:
            import ctypes
            hwnd = ctypes.windll.user32.GetForegroundWindow()
            app = pywinauto.Application(backend="uia").connect(handle=hwnd)
        dlg = app.top_window()
    except Exception:
        return []

    elements: list[dict[str, Any]] = []
    mark = [1]

    try:
        for ctrl in dlg.descendants():
            if mark[0] > 150:
                break
            try:
                ct = ctrl.friendly_class_name()
                if ct not in _WIN_INTERACTIVE_TYPES:
                    continue
                rect = ctrl.rectangle()
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                if w < 4 or h < 4:
                    continue
                elements.append({
                    "mark": mark[0],
                    "role": ct,
                    "label": (ctrl.window_text() or "").strip()[:60],
                    "x": rect.left, "y": rect.top,
                    "w": w, "h": h,
                })
                mark[0] += 1
            except Exception:
                pass
    except Exception:
        pass

    return elements
