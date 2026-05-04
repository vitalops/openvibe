"""Global pynput recorder with async screenshot capture.

Design principles
-----------------
- pynput listeners run in their own daemon threads (pynput creates these internally).
  We never touch the asyncio event loop from inside them.
- Screenshots are captured in a separate ThreadPoolExecutor (max 2 workers) so they
  never block the pynput threads or the TUI event loop.
- All shared state is protected by a single threading.Lock.
- Keyboard input is buffered and flushed as a single "type" event after a short
  inactivity window, or immediately before any mouse/special-key event.
- Modifier combos (Cmd+S, Ctrl+Z, …) are detected via on_press/on_release tracking.
"""

from __future__ import annotations

import base64
import io
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from openvibe.learn.trajectory import Trajectory, TrajectoryEvent

_KEY_FLUSH_DELAY = 0.55   # seconds of typing inactivity → flush buffer
_SS_MAX_W = 800
_SS_MAX_H = 500
_SS_QUALITY = 55           # JPEG quality — keeps images small (~30-60 KB each)

# Keys that should be recorded as explicit "key" events rather than buffered text
_SPECIAL_KEY_NAMES: dict = {}   # populated lazily after pynput import

_MODIFIER_NAMES = {
    "cmd", "cmd_l", "cmd_r",
    "ctrl", "ctrl_l", "ctrl_r",
    "alt", "alt_l", "alt_r",
    "shift", "shift_l", "shift_r",
}


class LearnRecorder:
    """Record global mouse + keyboard events with async screenshot capture.

    Thread safety
    -------------
    All public methods are safe to call from any thread.
    The pynput callbacks run in pynput's internal threads.
    Screenshot captures run in a ThreadPoolExecutor.
    None of these touch the asyncio event loop.
    """

    def __init__(self, task_name: str) -> None:
        self._task_name = task_name
        self._trajectory = Trajectory(task_name=task_name)
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="learn-ss"
        )
        self._pending: list[Future] = []

        # Keyboard buffering
        self._key_buffer: list[str] = []
        self._flush_timer: threading.Timer | None = None

        # Modifier-key tracking for combo detection
        self._held_mods: set[str] = set()

        # pynput listener handles
        self._mouse_listener = None
        self._keyboard_listener = None
        self._active = False

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Start global recording.  Raises RuntimeError if pynput is missing."""
        try:
            from pynput import keyboard, mouse
            _init_special_keys(keyboard)
        except ImportError:
            raise RuntimeError(
                "pynput is required for learn recording.\n"
                "  pip install pynput"
            )

        self._active = True
        # Capture the initial screen state asynchronously
        self._submit_screenshot(attach_to=None, is_initial=True)

        from pynput import keyboard as kb, mouse as ms

        self._mouse_listener = ms.Listener(
            on_click=self._on_click,
            on_scroll=self._on_scroll,
        )
        self._keyboard_listener = kb.Listener(
            on_press=self._on_key_press,
            on_release=self._on_key_release,
        )
        self._mouse_listener.start()
        self._keyboard_listener.start()

    def stop(self) -> Trajectory:
        """Stop recording, wait for in-flight screenshots, return trajectory."""
        self._active = False
        self._flush_key_buffer()

        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._keyboard_listener is not None:
            self._keyboard_listener.stop()

        # Wait for all pending screenshot captures (max 8 s each)
        for f in list(self._pending):
            try:
                f.result(timeout=8.0)
            except Exception:
                pass

        self._executor.shutdown(wait=False)
        self._trajectory.stopped_at = time.time()
        return self._trajectory

    # ------------------------------------------------------------------
    # pynput callbacks  (run in pynput's internal threads)
    # ------------------------------------------------------------------

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        if not pressed or not self._active:
            return
        self._flush_key_buffer()

        try:
            from pynput.mouse import Button
            action = "right_click" if button == Button.right else "click"
        except Exception:
            action = "click"

        event = TrajectoryEvent(
            timestamp=time.time(),
            action_type=action,
            x=int(x),
            y=int(y),
            button=str(button).split(".")[-1],
        )
        with self._lock:
            self._trajectory.events.append(event)
        self._submit_screenshot(attach_to=event)

    def _on_scroll(self, x: int, y: int, dx: int, dy: int) -> None:
        if not self._active:
            return
        # Consolidate consecutive scroll events at the same position
        with self._lock:
            if (
                self._trajectory.events
                and self._trajectory.events[-1].action_type == "scroll"
                and self._trajectory.events[-1].x == int(x)
                and self._trajectory.events[-1].y == int(y)
            ):
                ev = self._trajectory.events[-1]
                ev.scroll_dx += dx
                ev.scroll_dy += dy
                return
            event = TrajectoryEvent(
                timestamp=time.time(),
                action_type="scroll",
                x=int(x),
                y=int(y),
                scroll_dx=dx,
                scroll_dy=dy,
            )
            self._trajectory.events.append(event)

    def _on_key_press(self, key) -> None:
        if not self._active:
            return
        try:
            key_name = _key_name(key)

            # ── modifier keys: track but don't record individually ──
            if key_name in _MODIFIER_NAMES:
                with self._lock:
                    self._held_mods.add(_normalise_mod(key_name))
                return

            # ── modifier combo (e.g. cmd+s) ──
            with self._lock:
                mods = frozenset(self._held_mods)

            if mods:
                combo = "+".join(sorted(mods) + [key_name])
                self._flush_key_buffer()
                event = TrajectoryEvent(
                    timestamp=time.time(),
                    action_type="key",
                    key=combo,
                )
                with self._lock:
                    self._trajectory.events.append(event)
                return

            # ── special standalone keys ──
            if key_name in _SPECIAL_KEY_NAMES:
                self._flush_key_buffer()
                event = TrajectoryEvent(
                    timestamp=time.time(),
                    action_type="key",
                    key=_SPECIAL_KEY_NAMES[key_name],
                )
                with self._lock:
                    self._trajectory.events.append(event)
                # Capture screen after Enter / Escape (visible state change)
                if _SPECIAL_KEY_NAMES[key_name] in ("return", "escape"):
                    self._submit_screenshot(attach_to=event)
                return

            # ── regular printable character ──
            char = getattr(key, "char", None)
            if char and char.isprintable():
                self._cancel_flush_timer()
                with self._lock:
                    self._key_buffer.append(char)
                self._schedule_flush()

        except Exception:
            pass

    def _on_key_release(self, key) -> None:
        if not self._active:
            return
        try:
            key_name = _normalise_mod(_key_name(key))
            with self._lock:
                self._held_mods.discard(key_name)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Keyboard buffer helpers
    # ------------------------------------------------------------------

    def _schedule_flush(self) -> None:
        self._cancel_flush_timer()
        t = threading.Timer(_KEY_FLUSH_DELAY, self._flush_key_buffer)
        t.daemon = True
        self._flush_timer = t
        t.start()

    def _cancel_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None

    def _flush_key_buffer(self) -> None:
        self._cancel_flush_timer()
        with self._lock:
            if not self._key_buffer:
                return
            text = "".join(self._key_buffer)
            self._key_buffer.clear()
        event = TrajectoryEvent(
            timestamp=time.time(),
            action_type="type",
            text=text,
        )
        with self._lock:
            self._trajectory.events.append(event)

    # ------------------------------------------------------------------
    # Screenshot capture  (runs in ThreadPoolExecutor — off all hot paths)
    # ------------------------------------------------------------------

    def _submit_screenshot(
        self,
        attach_to: TrajectoryEvent | None,
        is_initial: bool = False,
    ) -> None:
        future = self._executor.submit(self._capture, attach_to, is_initial)
        with self._lock:
            # Prune completed futures to avoid unbounded memory growth
            self._pending = [f for f in self._pending if not f.done()]
            self._pending.append(future)

    def _capture(self, attach_to: TrajectoryEvent | None, is_initial: bool) -> None:
        try:
            import mss
            from PIL import Image

            with mss.mss() as sct:
                raw = sct.grab(sct.monitors[0])
                img = Image.frombytes("RGB", raw.size, raw.rgb)

            img.thumbnail((_SS_MAX_W, _SS_MAX_H), Image.LANCZOS)

            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=_SS_QUALITY, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode("ascii")

            if is_initial:
                self._trajectory.initial_screenshot = b64
            elif attach_to is not None:
                attach_to.screenshot_after = b64

        except Exception:
            pass  # screenshot failure is non-fatal; recording continues


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _init_special_keys(keyboard_module) -> None:
    """Populate _SPECIAL_KEY_NAMES from pynput.keyboard.Key on first use."""
    global _SPECIAL_KEY_NAMES
    if _SPECIAL_KEY_NAMES:
        return
    K = keyboard_module.Key
    _SPECIAL_KEY_NAMES = {
        "enter":     "return",
        "return":    "return",
        "esc":       "escape",
        "escape":    "escape",
        "tab":       "tab",
        "backspace": "backspace",
        "delete":    "delete",
        "up":        "up",
        "down":      "down",
        "left":      "left",
        "right":     "right",
        "home":      "home",
        "end":       "end",
        "page_up":   "page_up",
        "page_down": "page_down",
        "f1": "f1",  "f2": "f2",  "f3": "f3",  "f4": "f4",
        "f5": "f5",  "f6": "f6",  "f7": "f7",  "f8": "f8",
        "f9": "f9",  "f10": "f10", "f11": "f11", "f12": "f12",
        "space": "space",
    }


def _key_name(key) -> str:
    """Return a normalised lowercase string name for a pynput key."""
    name = str(key)
    # Key.enter → "enter", Key.esc → "esc"
    if name.startswith("Key."):
        return name[4:].lower()
    # KeyCode with vk → "vk_NNN"
    if hasattr(key, "vk") and key.vk and not hasattr(key, "char"):
        return f"vk_{key.vk}"
    # Regular char key: key.char is the character
    if hasattr(key, "char") and key.char:
        return key.char.lower()
    return name.lower().strip("'")


def _normalise_mod(name: str) -> str:
    """Map cmd_l / cmd_r → cmd, ctrl_l → ctrl, etc."""
    return name.split("_")[0] if "_" in name and name.split("_")[1] in ("l", "r") else name
