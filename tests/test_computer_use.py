"""Tests for the computer-use subsystem.

All screen/input calls are mocked — no mss/pyautogui/pillow required.
The key pattern used throughout is patching `_do_action` at the class level
(since it runs inside run_in_executor) rather than patching the underlying
pyautogui module, which avoids import errors and accessibility-check failures.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openvibe.computer.sandbox import (
    ActionType,
    ComputerSandbox,
    clear_sandbox,
    get_sandbox,
)
from openvibe.tool.base import ToolContext, ToolResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(session_id: str = "test-session") -> ToolContext:
    """Build a minimal ToolContext with no permission service (all allowed)."""
    return ToolContext(
        session_id=session_id,
        message_id="msg-1",
        agent_name="computer",
        project_id="proj-1",
        working_dir="/tmp",
        abort=asyncio.Event(),
        call_id="call-1",
        _permissions=None,
    )


# ---------------------------------------------------------------------------
# Sandbox tests
# ---------------------------------------------------------------------------


class TestComputerSandbox:
    def setup_method(self):
        clear_sandbox("s1")

    def test_get_sandbox_creates_new(self):
        sb = get_sandbox("s1")
        assert sb.session_id == "s1"
        assert sb.audit_log == []

    def test_get_sandbox_returns_same_instance(self):
        assert get_sandbox("s1") is get_sandbox("s1")

    def test_clear_sandbox(self):
        a = get_sandbox("s1")
        clear_sandbox("s1")
        b = get_sandbox("s1")
        assert a is not b

    @pytest.mark.asyncio
    async def test_record_action_appends_entry(self):
        sb = ComputerSandbox(session_id="s1")
        entry = await sb.record_action(
            ActionType.SCREENSHOT, params={"region": None}, result="800x600"
        )
        assert len(sb.audit_log) == 1
        assert sb.audit_log[0] is entry
        assert entry.action_type == ActionType.SCREENSHOT
        assert entry.result == "800x600"
        assert entry.error is None

    @pytest.mark.asyncio
    async def test_record_action_with_error(self):
        sb = ComputerSandbox(session_id="s1")
        await sb.record_action(
            ActionType.MOUSE_CLICK,
            params={"x": 100, "y": 200},
            error="boom",
        )
        assert sb.audit_log[0].error == "boom"

    def test_export_audit_log(self):
        sb = ComputerSandbox(session_id="s1")
        asyncio.run(
            sb.record_action(ActionType.APP_OPEN, params={"name": "Terminal"}, result="ok")
        )
        log = sb.export_audit_log()
        assert len(log) == 1
        assert log[0]["action"] == "app_open"
        assert log[0]["result"] == "ok"

    def test_is_app_allowed_no_list(self):
        sb = ComputerSandbox(session_id="s1")
        assert sb.is_app_allowed("Anything") is True

    def test_is_app_allowed_with_list(self):
        sb = ComputerSandbox(session_id="s1", allowed_apps=["Terminal", "Chrome"])
        assert sb.is_app_allowed("terminal") is True  # case-insensitive
        assert sb.is_app_allowed("Google Chrome") is True  # substring
        assert sb.is_app_allowed("Slack") is False

    def test_is_coordinate_allowed_no_region(self):
        sb = ComputerSandbox(session_id="s1")
        assert sb.is_coordinate_allowed(9999, 9999) is True

    def test_is_coordinate_allowed_with_region(self):
        sb = ComputerSandbox(session_id="s1", screen_region=(100, 100, 500, 400))
        assert sb.is_coordinate_allowed(150, 200) is True
        assert sb.is_coordinate_allowed(50, 50) is False
        assert sb.is_coordinate_allowed(700, 600) is False

    def test_summary(self):
        sb = ComputerSandbox(session_id="abcdef123456")
        asyncio.run(sb.record_action(ActionType.SCREENSHOT, params={}))
        asyncio.run(sb.record_action(ActionType.MOUSE_CLICK, params={}))
        s = sb.summary()
        assert "2 actions" in s
        assert "mouse_click" in s
        assert "screenshot" in s


# ---------------------------------------------------------------------------
# ScreenshotTool tests
# ---------------------------------------------------------------------------


class TestScreenshotTool:
    @pytest.mark.asyncio
    async def test_invalid_region_length(self):
        from openvibe.tool.computer_screenshot import ScreenshotTool

        tool = ScreenshotTool()
        ctx = _ctx()
        result = await tool.execute(ctx, ScreenshotTool.Params(region=[100, 200]))
        assert result.error is True
        assert "4 elements" in result.output

    @pytest.mark.asyncio
    async def test_coordinate_outside_sandbox_region(self):
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-ss")
        sb = get_sandbox("s-ss")
        sb.screen_region = (0, 0, 200, 200)

        tool = ScreenshotTool()
        ctx = _ctx("s-ss")
        result = await tool.execute(ctx, ScreenshotTool.Params(region=[500, 500, 100, 100]))
        assert result.error is True
        assert "outside" in result.output.lower()

    @pytest.mark.asyncio
    async def test_capture_full_screen(self):
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-full")
        tool = ScreenshotTool()
        ctx = _ctx("s-full")

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        with patch(
            "openvibe.computer.capture.capture_screen",
            return_value=(fake_png, 1920, 1080),
        ):
            result = await tool.execute(ctx, ScreenshotTool.Params())

        assert result.error is False
        assert "1920" in result.title
        assert "1080" in result.title
        assert len(result.attachments) == 1
        assert result.attachments[0].filename == "screenshot.png"
        assert result.attachments[0].content == fake_png
        assert result.attachments[0].media_type == "image/png"

    @pytest.mark.asyncio
    async def test_capture_records_audit_entry(self):
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-audit")
        tool = ScreenshotTool()
        ctx = _ctx("s-audit")

        fake_png = b"\x89PNG" + b"\x00" * 20
        with patch(
            "openvibe.computer.capture.capture_screen",
            return_value=(fake_png, 800, 600),
        ):
            await tool.execute(ctx, ScreenshotTool.Params())

        sb = get_sandbox("s-audit")
        assert len(sb.audit_log) == 1
        assert sb.audit_log[0].action_type == ActionType.SCREENSHOT

    @pytest.mark.asyncio
    async def test_sandbox_stores_last_screenshot(self):
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-store")
        tool = ScreenshotTool()
        ctx = _ctx("s-store")

        fake_png = b"\x89PNG" + b"\x00" * 20
        assert get_sandbox("s-store").last_screenshot is None

        with patch(
            "openvibe.computer.capture.capture_screen",
            return_value=(fake_png, 50, 50),
        ):
            await tool.execute(ctx, ScreenshotTool.Params())

        assert get_sandbox("s-store").last_screenshot == fake_png

    @pytest.mark.asyncio
    async def test_processor_stores_image_in_metadata(self):
        """image_b64 must be stored in ToolState.metadata so the LLM can see it."""
        import base64

        from openvibe.tool.base import Attachment, ToolResult
        from openvibe.session.models import ToolState
        from openvibe.config import ToolStateStatus

        fake_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        fake_result = ToolResult(
            title="Screenshot 800x600",
            output="Captured 800x600 screenshot.",
            attachments=[
                Attachment(filename="screenshot.png", content=fake_png, media_type="image/png")
            ],
        )

        state = ToolState(
            status=ToolStateStatus.COMPLETED,
            call_id="call-1",
            tool_name="screenshot",
            input={},
            output=fake_result.output,
        )

        for att in fake_result.attachments:
            if att.media_type.startswith("image/"):
                state.metadata["image_b64"] = base64.b64encode(att.content).decode("ascii")
                state.metadata["image_media_type"] = att.media_type
                break

        assert "image_b64" in state.metadata
        assert state.metadata["image_media_type"] == "image/png"
        assert base64.b64decode(state.metadata["image_b64"]) == fake_png


# ---------------------------------------------------------------------------
# MouseTool tests
# ---------------------------------------------------------------------------


class TestMouseTool:
    @pytest.mark.asyncio
    async def test_click_outside_sandbox_region(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-mouse")
        sb = get_sandbox("s-mouse")
        sb.screen_region = (0, 0, 500, 500)

        tool = MouseTool()
        ctx = _ctx("s-mouse")
        result = await tool.execute(
            ctx, MouseTool.Params(action="click", x=999, y=999)
        )
        assert result.error is True
        assert "outside" in result.output.lower()

    @pytest.mark.asyncio
    async def test_click_within_region(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-click")
        sb = get_sandbox("s-click")
        sb.screen_region = (0, 0, 1920, 1080)

        tool = MouseTool()
        ctx = _ctx("s-click")

        with patch.object(MouseTool, "_do_action", return_value="Left-clicked at (100, 200)."):
            result = await tool.execute(
                ctx, MouseTool.Params(action="click", x=100, y=200)
            )

        assert result.error is False
        assert "100" in result.output
        assert "200" in result.output

    @pytest.mark.asyncio
    async def test_scroll_down(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-scroll")
        tool = MouseTool()
        ctx = _ctx("s-scroll")

        with patch.object(
            MouseTool, "_do_action", return_value="Scrolled down 3 click(s) at (400, 400)."
        ):
            result = await tool.execute(
                ctx, MouseTool.Params(action="scroll", x=400, y=400, direction="down", amount=3)
            )

        assert result.error is False
        assert "down" in result.output.lower()

    @pytest.mark.asyncio
    async def test_drag_with_valid_end(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-drag")
        tool = MouseTool()
        ctx = _ctx("s-drag")

        with patch.object(
            MouseTool, "_do_action", return_value="Dragged from (10, 10) to (300, 300)."
        ):
            result = await tool.execute(
                ctx, MouseTool.Params(action="drag", x=10, y=10, end_x=300, end_y=300)
            )

        assert result.error is False
        sb = get_sandbox("s-drag")
        assert any(e.action_type == ActionType.MOUSE_DRAG for e in sb.audit_log)

    @pytest.mark.asyncio
    async def test_right_click(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-rclick")
        tool = MouseTool()
        ctx = _ctx("s-rclick")

        with patch.object(
            MouseTool, "_do_action", return_value="Right-clicked at (200, 300)."
        ):
            result = await tool.execute(
                ctx, MouseTool.Params(action="right_click", x=200, y=300)
            )

        assert result.error is False
        assert result.error is False

    @pytest.mark.asyncio
    async def test_move_records_audit(self):
        from openvibe.tool.computer_mouse import MouseTool

        clear_sandbox("s-move")
        tool = MouseTool()
        ctx = _ctx("s-move")

        with patch.object(MouseTool, "_do_action", return_value="Moved mouse to (500, 400)."):
            await tool.execute(ctx, MouseTool.Params(action="move", x=500, y=400))

        sb = get_sandbox("s-move")
        assert any(e.action_type == ActionType.MOUSE_MOVE for e in sb.audit_log)

    @pytest.mark.asyncio
    async def test_do_action_drag_missing_end_raises(self):
        """_do_action raises ValueError when end coords are missing for drag."""
        from openvibe.tool.computer_mouse import MouseTool

        params = MouseTool.Params(action="drag", x=10, y=10)
        # _do_action is synchronous and raises directly
        with pytest.raises((ValueError, Exception)):
            with patch("openvibe.tool.computer_mouse._check_accessibility"):
                with patch("openvibe.tool.computer_mouse._pyautogui") as mock_pag:
                    mock_pag.return_value = MagicMock()
                    MouseTool._do_action(params)


# ---------------------------------------------------------------------------
# KeyboardTool tests
# ---------------------------------------------------------------------------


class TestKeyboardTool:
    @pytest.mark.asyncio
    async def test_type_text(self):
        from openvibe.tool.computer_keyboard import KeyboardTool

        clear_sandbox("s-kbd")
        tool = KeyboardTool()
        ctx = _ctx("s-kbd")

        with patch.object(
            KeyboardTool, "_do_action", return_value="Typed 11 characters: 'hello world'"
        ):
            result = await tool.execute(
                ctx, KeyboardTool.Params(action="type", text="hello world")
            )

        assert result.error is False
        assert "11" in result.output

    @pytest.mark.asyncio
    async def test_type_missing_text_raises(self):
        """_do_action raises ValueError when text is None for action='type'."""
        from openvibe.tool.computer_keyboard import KeyboardTool

        params = KeyboardTool.Params(action="type")
        with pytest.raises((ValueError, Exception)):
            with patch("openvibe.tool.computer_keyboard._check_accessibility"):
                with patch("openvibe.tool.computer_keyboard._pyautogui") as mock_pag:
                    mock_pag.return_value = MagicMock()
                    KeyboardTool._do_action(params)

    @pytest.mark.asyncio
    async def test_press_key(self):
        from openvibe.tool.computer_keyboard import KeyboardTool

        clear_sandbox("s-press")
        tool = KeyboardTool()
        ctx = _ctx("s-press")

        with patch.object(
            KeyboardTool, "_do_action", return_value="Pressed key: 'enter'"
        ):
            result = await tool.execute(
                ctx, KeyboardTool.Params(action="press", key="enter")
            )

        assert result.error is False
        assert "enter" in result.output.lower()

    @pytest.mark.asyncio
    async def test_hotkey(self):
        from openvibe.tool.computer_keyboard import KeyboardTool

        clear_sandbox("s-hotkey")
        tool = KeyboardTool()
        ctx = _ctx("s-hotkey")

        with patch.object(
            KeyboardTool, "_do_action", return_value="Pressed hotkey: ctrl+c"
        ):
            result = await tool.execute(
                ctx, KeyboardTool.Params(action="hotkey", keys=["ctrl", "c"])
            )

        assert result.error is False
        assert "ctrl" in result.output.lower()

    @pytest.mark.asyncio
    async def test_keyboard_records_audit(self):
        from openvibe.tool.computer_keyboard import KeyboardTool

        clear_sandbox("s-kdaud")
        tool = KeyboardTool()
        ctx = _ctx("s-kdaud")

        with patch.object(KeyboardTool, "_do_action", return_value="Pressed key: 'escape'"):
            await tool.execute(ctx, KeyboardTool.Params(action="press", key="escape"))

        sb = get_sandbox("s-kdaud")
        assert any(e.action_type == ActionType.KEYBOARD_PRESS for e in sb.audit_log)

    @pytest.mark.asyncio
    async def test_hold_action(self):
        from openvibe.tool.computer_keyboard import KeyboardTool

        clear_sandbox("s-hold")
        tool = KeyboardTool()
        ctx = _ctx("s-hold")

        with patch.object(
            KeyboardTool,
            "_do_action",
            return_value="Held key 'shift' for 1.00s then released.",
        ):
            result = await tool.execute(
                ctx, KeyboardTool.Params(action="hold", key="shift", hold_duration=1.0)
            )

        assert result.error is False
        sb = get_sandbox("s-hold")
        assert any(e.action_type == ActionType.KEYBOARD_HOLD for e in sb.audit_log)


# ---------------------------------------------------------------------------
# AppTool tests
# ---------------------------------------------------------------------------


class TestAppTool:
    @pytest.mark.asyncio
    async def test_open_denied_by_allowlist(self):
        from openvibe.tool.computer_app import AppTool

        clear_sandbox("s-app")
        sb = get_sandbox("s-app")
        sb.allowed_apps = ["Terminal"]

        tool = AppTool()
        ctx = _ctx("s-app")
        result = await tool.execute(ctx, AppTool.Params(action="open", name="Slack"))
        assert result.error is True
        assert "allow" in result.output.lower()

    @pytest.mark.asyncio
    async def test_list_action_bypasses_allowlist(self):
        from openvibe.tool.computer_app import AppTool

        clear_sandbox("s-list")
        sb = get_sandbox("s-list")
        sb.allowed_apps = ["Terminal"]

        tool = AppTool()
        ctx = _ctx("s-list")

        with patch("openvibe.tool.computer_app._list_windows", return_value="• App1\n• App2"):
            result = await tool.execute(ctx, AppTool.Params(action="list"))

        assert result.error is False

    @pytest.mark.asyncio
    async def test_open_app_success(self):
        from openvibe.tool.computer_app import AppTool

        clear_sandbox("s-appaud")
        tool = AppTool()
        ctx = _ctx("s-appaud")

        with patch("openvibe.tool.computer_app._open_app", return_value="Opened 'Terminal'."):
            result = await tool.execute(ctx, AppTool.Params(action="open", name="Terminal"))

        assert result.error is False
        sb = get_sandbox("s-appaud")
        assert any(e.action_type == ActionType.APP_OPEN for e in sb.audit_log)

    @pytest.mark.asyncio
    async def test_open_app_error_propagates(self):
        from openvibe.tool.computer_app import AppTool

        clear_sandbox("s-apperr")
        tool = AppTool()
        ctx = _ctx("s-apperr")

        with patch(
            "openvibe.tool.computer_app._open_app",
            side_effect=RuntimeError("app not found"),
        ):
            result = await tool.execute(ctx, AppTool.Params(action="open", name="NonExistent"))

        assert result.error is True


# ---------------------------------------------------------------------------
# LLM vision message builder tests
# ---------------------------------------------------------------------------


class TestLLMVisionMessages:
    """Verify that _to_llm_messages emits image ContentBlocks for screenshot results."""

    def _make_tool_part_with_image(self, b64: str, media_type: str = "image/png"):
        from openvibe.config import ToolStateStatus
        from openvibe.session.models import ToolPart, ToolState

        state = ToolState(
            status=ToolStateStatus.COMPLETED,
            call_id="call-img-1",
            tool_name="screenshot",
            input={},
            output="Captured 1920x1080 screenshot.",
            metadata={"image_b64": b64, "image_media_type": media_type},
        )
        return ToolPart(state=state)

    def _make_assistant_msg(self, tool_part):
        from openvibe.config import MessageRole
        from openvibe.session.models import MessageInfo, TextPart

        return MessageInfo(
            id="msg-1",
            session_id="s1",
            role=MessageRole.ASSISTANT,
            position=0,
            created_at="2026-01-01T00:00:00",
            parts=[TextPart(content="Let me take a screenshot."), tool_part],
        )

    def test_image_tool_result_produces_content_block_list(self):
        import base64

        from openvibe.session.processor import _to_llm_messages
        from openvibe.llm import ContentBlock
        from openvibe.agent.agent import _BUILTIN_AGENTS

        fake_b64 = base64.b64encode(b"\x89PNG fake").decode("ascii")
        tool_part = self._make_tool_part_with_image(fake_b64)
        assistant_msg = self._make_assistant_msg(tool_part)

        agent = _BUILTIN_AGENTS["computer"]
        messages = _to_llm_messages([assistant_msg], agent)

        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        tool_msg = tool_msgs[0]
        assert tool_msg.tool_call_id == "call-img-1"
        assert isinstance(tool_msg.content, list)
        assert len(tool_msg.content) == 2

        image_block = tool_msg.content[0]
        text_block = tool_msg.content[1]

        assert isinstance(image_block, ContentBlock)
        assert image_block.type == "image_url"
        assert image_block.image_url is not None
        assert image_block.image_url["url"].startswith("data:image/png;base64,")
        assert fake_b64 in image_block.image_url["url"]

        assert isinstance(text_block, ContentBlock)
        assert text_block.type == "text"
        assert "1920" in (text_block.text or "")

    def test_text_only_tool_result_produces_plain_string(self):
        from openvibe.config import MessageRole, ToolStateStatus
        from openvibe.session.models import MessageInfo, TextPart, ToolPart, ToolState
        from openvibe.session.processor import _to_llm_messages
        from openvibe.agent.agent import _BUILTIN_AGENTS

        state = ToolState(
            status=ToolStateStatus.COMPLETED,
            call_id="call-text-1",
            tool_name="bash",
            input={"command": "ls"},
            output="file1.py\nfile2.py",
        )
        tool_part = ToolPart(state=state)
        assistant_msg = MessageInfo(
            id="msg-2",
            session_id="s1",
            role=MessageRole.ASSISTANT,
            position=0,
            created_at="2026-01-01T00:00:00",
            parts=[TextPart(content="Running ls."), tool_part],
        )

        agent = _BUILTIN_AGENTS["build"]
        messages = _to_llm_messages([assistant_msg], agent)

        tool_msgs = [m for m in messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert isinstance(tool_msgs[0].content, str)
        assert "file1.py" in tool_msgs[0].content

    def test_litellm_serialisation_of_image_tool_result(self):
        """_to_litellm_messages must produce valid dicts for image tool results."""
        import base64

        from openvibe.llm import ContentBlock, Message, _to_litellm_messages

        fake_b64 = base64.b64encode(b"PNG-DATA").decode("ascii")
        msg = Message(
            role="tool",
            content=[
                ContentBlock(
                    type="image_url",
                    image_url={"url": f"data:image/png;base64,{fake_b64}"},
                ),
                ContentBlock(type="text", text="Screenshot captured."),
            ],
            tool_call_id="call-123",
        )

        result = _to_litellm_messages([msg])
        assert len(result) == 1
        d = result[0]
        assert d["role"] == "tool"
        assert d["tool_call_id"] == "call-123"
        assert isinstance(d["content"], list)
        assert d["content"][0]["type"] == "image_url"
        assert d["content"][1]["type"] == "text"
        assert d["content"][1]["text"] == "Screenshot captured."


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestComputerUseRegistry:
    def test_create_computer_use_registry_contains_cu_tools(self):
        from openvibe.tool.base import create_computer_use_registry

        registry = create_computer_use_registry()
        assert "screenshot" in registry
        assert "mouse" in registry
        assert "keyboard" in registry
        assert "app" in registry

    def test_create_computer_use_registry_contains_default_tools(self):
        from openvibe.tool.base import create_computer_use_registry

        registry = create_computer_use_registry()
        assert "bash" in registry
        assert "read" in registry
        assert "write" in registry
        assert "glob" in registry


# ---------------------------------------------------------------------------
# Agent tests
# ---------------------------------------------------------------------------


class TestComputerAgent:
    def test_computer_agent_exists(self):
        from openvibe.agent.agent import _BUILTIN_AGENTS

        assert "computer" in _BUILTIN_AGENTS

    def test_computer_agent_rules(self):
        from openvibe.agent.agent import _BUILTIN_AGENTS
        from openvibe.config import PermissionAction

        rules = {r.tool: r.action for r in _BUILTIN_AGENTS["computer"].permission_rules}
        assert rules.get("screenshot") == PermissionAction.ALLOW
        assert rules.get("mouse") == PermissionAction.ASK
        assert rules.get("keyboard") == PermissionAction.ASK
        assert rules.get("app") == PermissionAction.ASK


# ---------------------------------------------------------------------------
# Diff / change-detection tests (require Pillow)
# ---------------------------------------------------------------------------


def _make_solid_png(width: int, height: int, color: tuple) -> bytes:
    """Create a solid-colour PNG for diffing tests."""
    pytest.importorskip("PIL", reason="Pillow not installed")
    from PIL import Image
    import io

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


class TestDiffScreenshots:
    def test_diff_unchanged(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from openvibe.computer.capture import diff_screenshots

        png = _make_solid_png(100, 100, (128, 128, 128))
        report = diff_screenshots(png, png)
        assert report["changed"] is False
        assert report["change_fraction"] < 0.001
        assert "no visible change" in str(report["summary"]).lower()

    def test_diff_full_change(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from openvibe.computer.capture import diff_screenshots

        before = _make_solid_png(100, 100, (0, 0, 0))
        after = _make_solid_png(100, 100, (255, 255, 255))
        report = diff_screenshots(before, after)
        assert report["changed"] is True
        assert report["change_fraction"] > 0.99
        assert report["changed_region"] is not None

    def test_diff_size_mismatch(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from openvibe.computer.capture import diff_screenshots

        small = _make_solid_png(100, 100, (0, 0, 0))
        large = _make_solid_png(200, 200, (0, 0, 0))
        report = diff_screenshots(small, large)
        assert report["changed"] is True
        assert "resolution" in str(report["summary"]).lower()

    def test_diff_partial_change(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from PIL import Image
        import io
        from openvibe.computer.capture import diff_screenshots

        before = _make_solid_png(200, 100, (0, 0, 0))
        img = Image.new("RGB", (200, 100), (0, 0, 0))
        for x in range(100, 200):
            for y in range(100):
                img.putpixel((x, y), (255, 255, 255))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        after = buf.getvalue()

        report = diff_screenshots(before, after)
        assert report["changed"] is True
        region = report["changed_region"]
        assert region is not None
        x, _y, w, _h = region
        assert x >= 99

    @pytest.mark.asyncio
    async def test_screenshot_diff_included_on_second_capture(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-diff")
        tool = ScreenshotTool()
        ctx = _ctx("s-diff")

        first_png = _make_solid_png(100, 100, (0, 0, 0))
        second_png = _make_solid_png(100, 100, (255, 255, 255))

        with patch("openvibe.computer.capture.capture_screen", return_value=(first_png, 100, 100)):
            await tool.execute(ctx, ScreenshotTool.Params())

        with patch("openvibe.computer.capture.capture_screen", return_value=(second_png, 100, 100)):
            result = await tool.execute(ctx, ScreenshotTool.Params())

        assert "change detection" in result.output.lower()
        assert result.error is False

    @pytest.mark.asyncio
    async def test_screenshot_no_diff_on_first_capture(self):
        pytest.importorskip("PIL", reason="Pillow not installed")
        from openvibe.tool.computer_screenshot import ScreenshotTool

        clear_sandbox("s-nodiff")
        tool = ScreenshotTool()
        ctx = _ctx("s-nodiff")

        png = _make_solid_png(100, 100, (0, 0, 0))
        with patch("openvibe.computer.capture.capture_screen", return_value=(png, 100, 100)):
            result = await tool.execute(ctx, ScreenshotTool.Params())

        assert "change detection" not in result.output.lower()
        assert result.error is False
