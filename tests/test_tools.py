"""Tests for filesystem tools: WriteTool, EditTool, and the _resolve helper.

Focuses on:
- Bare filename rejection (no implicit working directory)
- Absolute path handling
- Relative path (./...) handling
- Edit string-not-found and ambiguous-string errors
- Successful write and edit round-trips
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openvibe.tool.base import ToolContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ctx(working_dir: str = "/tmp") -> ToolContext:
    return ToolContext(
        session_id="test",
        message_id="msg-1",
        agent_name="build",
        project_id="proj-1",
        working_dir=working_dir,
        abort=asyncio.Event(),
        call_id="call-1",
        _permissions=None,  # no permission check
    )


# ---------------------------------------------------------------------------
# _resolve helper (write.py and edit.py share the same logic)
# ---------------------------------------------------------------------------


class TestResolveWrite:
    def _resolve(self, path_str: str, working_dir: str = "/tmp") -> Path | None:
        from openvibe.tool.write import _resolve
        return _resolve(path_str, working_dir)

    def test_absolute_path_returned_as_is(self):
        result = self._resolve("/tmp/output.py")
        assert result == Path("/tmp/output.py")

    def test_dot_slash_relative(self):
        result = self._resolve("./subdir/file.py", "/project")
        assert result == Path("/project/subdir/file.py")

    def test_dot_dot_relative(self):
        result = self._resolve("../other/file.py", "/project/src")
        assert result == Path("/project/src/../other/file.py")

    def test_subdirectory_relative(self):
        """path with a directory component (e.g. 'src/main.py') is allowed."""
        result = self._resolve("src/main.py", "/project")
        assert result == Path("/project/src/main.py")

    def test_bare_filename_returns_none(self):
        assert self._resolve("output.docx") is None
        assert self._resolve("README.md") is None
        assert self._resolve("main.py") is None

    def test_bare_filename_with_extension(self):
        assert self._resolve("report.pdf") is None


class TestResolveEdit:
    def _resolve(self, path_str: str, working_dir: str = "/tmp") -> Path | None:
        from openvibe.tool.edit import _resolve
        return _resolve(path_str, working_dir)

    def test_absolute_path(self):
        result = self._resolve("/etc/hosts")
        assert result == Path("/etc/hosts")

    def test_bare_filename_returns_none(self):
        assert self._resolve("foo.py") is None

    def test_dot_slash_relative(self):
        result = self._resolve("./foo.py", "/workspace")
        assert result == Path("/workspace/foo.py")


# ---------------------------------------------------------------------------
# WriteTool
# ---------------------------------------------------------------------------


class TestWriteTool:
    @pytest.mark.asyncio
    async def test_bare_filename_rejected(self):
        from openvibe.tool.write import WriteTool

        tool = WriteTool()
        ctx = _ctx()
        result = await tool.execute(
            ctx, WriteTool.Params(path="output.txt", content="hello")
        )
        assert result.error is True
        assert "bare filename" in result.output.lower()

    @pytest.mark.asyncio
    async def test_write_creates_file(self, tmp_path):
        from openvibe.tool.write import WriteTool

        tool = WriteTool()
        target = tmp_path / "new_file.py"
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx, WriteTool.Params(path=str(target), content="print('hello')\n")
        )
        assert result.error is False
        assert target.read_text() == "print('hello')\n"
        assert "Created" in result.title

    @pytest.mark.asyncio
    async def test_write_overwrites_existing(self, tmp_path):
        from openvibe.tool.write import WriteTool

        target = tmp_path / "existing.py"
        target.write_text("old content")

        tool = WriteTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx, WriteTool.Params(path=str(target), content="new content")
        )
        assert result.error is False
        assert target.read_text() == "new content"
        assert "Updated" in result.title

    @pytest.mark.asyncio
    async def test_write_creates_parent_dirs(self, tmp_path):
        from openvibe.tool.write import WriteTool

        target = tmp_path / "deep" / "nested" / "file.txt"
        tool = WriteTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx, WriteTool.Params(path=str(target), content="data")
        )
        assert result.error is False
        assert target.exists()

    @pytest.mark.asyncio
    async def test_write_relative_dot_slash(self, tmp_path):
        from openvibe.tool.write import WriteTool

        tool = WriteTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx, WriteTool.Params(path="./output.txt", content="hi\n")
        )
        assert result.error is False
        assert (tmp_path / "output.txt").read_text() == "hi\n"

    @pytest.mark.asyncio
    async def test_write_reports_line_count(self, tmp_path):
        from openvibe.tool.write import WriteTool

        tool = WriteTool()
        target = tmp_path / "lines.py"
        ctx = _ctx(str(tmp_path))
        content = "line1\nline2\nline3\n"
        result = await tool.execute(
            ctx, WriteTool.Params(path=str(target), content=content)
        )
        assert result.error is False
        assert "3" in result.output  # 3 lines


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------


class TestEditTool:
    @pytest.mark.asyncio
    async def test_bare_filename_rejected(self):
        from openvibe.tool.edit import EditTool

        tool = EditTool()
        ctx = _ctx()
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path="main.py",
                edits=[{"old_string": "old", "new_string": "new"}],
            ),
        )
        assert result.error is True
        assert "bare filename" in result.output.lower()

    @pytest.mark.asyncio
    async def test_file_not_found(self, tmp_path):
        from openvibe.tool.edit import EditTool

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(tmp_path / "nonexistent.py"),
                edits=[{"old_string": "old", "new_string": "new"}],
            ),
        )
        assert result.error is True
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_string_not_found(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "code.py"
        target.write_text("def hello():\n    pass\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(target),
                edits=[{"old_string": "does_not_exist", "new_string": "replacement"}],
            ),
        )
        assert result.error is True
        assert "not found" in result.output.lower()

    @pytest.mark.asyncio
    async def test_ambiguous_string_rejected(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "dup.py"
        target.write_text("pass\npass\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(target),
                edits=[{"old_string": "pass", "new_string": "return"}],
            ),
        )
        assert result.error is True
        assert "2 times" in result.output or "appears" in result.output.lower()

    @pytest.mark.asyncio
    async def test_successful_edit(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "code.py"
        target.write_text("def hello():\n    return 'hi'\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(target),
                edits=[{"old_string": "return 'hi'", "new_string": "return 'hello'"}],
            ),
        )
        assert result.error is False
        assert "return 'hello'" in target.read_text()

    @pytest.mark.asyncio
    async def test_multiple_edits_applied_in_order(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "multi.py"
        target.write_text("foo = 1\nbar = 2\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(target),
                edits=[
                    {"old_string": "foo = 1", "new_string": "foo = 10"},
                    {"old_string": "bar = 2", "new_string": "bar = 20"},
                ],
            ),
        )
        assert result.error is False
        content = target.read_text()
        assert "foo = 10" in content
        assert "bar = 20" in content

    @pytest.mark.asyncio
    async def test_no_change_when_identical(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "same.py"
        target.write_text("x = 1\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path=str(target),
                edits=[{"old_string": "x = 1", "new_string": "x = 1"}],
            ),
        )
        assert result.error is False
        assert "no changes" in result.output.lower()

    @pytest.mark.asyncio
    async def test_relative_dot_slash_path(self, tmp_path):
        from openvibe.tool.edit import EditTool

        target = tmp_path / "rel.py"
        target.write_text("hello = True\n")

        tool = EditTool()
        ctx = _ctx(str(tmp_path))
        result = await tool.execute(
            ctx,
            EditTool.Params(
                path="./rel.py",
                edits=[{"old_string": "hello = True", "new_string": "hello = False"}],
            ),
        )
        assert result.error is False
        assert "hello = False" in target.read_text()
