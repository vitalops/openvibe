"""Tests for openvibe.permission.permission — _matches, exceptions, PermissionService."""

from __future__ import annotations

import pytest

from openvibe.config import PermissionAction
from openvibe.permission.permission import (
    PermissionDenied,
    PermissionRejected,
    Rule,
    _matches,
)

# ---------------------------------------------------------------------------
# _matches — tool name matching
# ---------------------------------------------------------------------------


def test_matches_exact_tool_name():
    rule = Rule(tool="bash", action=PermissionAction.ALLOW)
    assert _matches(rule, "bash")


def test_matches_glob_tool_name():
    rule = Rule(tool="file.*", action=PermissionAction.ALLOW)
    assert _matches(rule, "file.read")
    assert _matches(rule, "file.write")


def test_matches_no_match_returns_false():
    rule = Rule(tool="bash", action=PermissionAction.ALLOW)
    assert not _matches(rule, "read")


def test_matches_glob_no_match():
    rule = Rule(tool="file.*", action=PermissionAction.ALLOW)
    assert not _matches(rule, "bash")


def test_matches_with_pattern_and_matching_argument():
    rule = Rule(tool="bash", action=PermissionAction.ASK, pattern="*.sh")
    assert _matches(rule, "bash", argument="deploy.sh")


def test_matches_with_pattern_no_argument_still_matches():
    """When a pattern is set but no argument is given the rule still matches the tool."""
    rule = Rule(tool="bash", action=PermissionAction.ASK, pattern="*.sh")
    # argument=None — no path to match, so pattern check is skipped and it matches
    assert _matches(rule, "bash", argument=None)


def test_matches_with_pattern_wrong_argument():
    rule = Rule(tool="bash", action=PermissionAction.ASK, pattern="*.sh")
    assert not _matches(rule, "bash", argument="README.md")


def test_matches_wildcard_tool():
    rule = Rule(tool="*", action=PermissionAction.ALLOW)
    assert _matches(rule, "anything")
    assert _matches(rule, "bash")


# ---------------------------------------------------------------------------
# PermissionDenied
# ---------------------------------------------------------------------------


def test_permission_denied_str():
    exc = PermissionDenied("bash")
    assert "bash" in str(exc)
    assert exc.tool == "bash"
    assert exc.pattern is None


def test_permission_denied_with_pattern():
    exc = PermissionDenied("bash", pattern="*.sh")
    assert exc.pattern == "*.sh"


# ---------------------------------------------------------------------------
# PermissionRejected
# ---------------------------------------------------------------------------


def test_permission_rejected_str():
    exc = PermissionRejected("write")
    assert "write" in str(exc)
    assert exc.tool == "write"


def test_permission_denied_is_exception():
    with pytest.raises(PermissionDenied):
        raise PermissionDenied("bash")


def test_permission_rejected_is_exception():
    with pytest.raises(PermissionRejected):
        raise PermissionRejected("edit")


def test_matches_with_empty_string_argument_skips_pattern():
    """Empty string argument is falsy — pattern check is skipped, rule matches."""
    rule = Rule(tool="bash", action=PermissionAction.ASK, pattern="*.sh")
    # argument="" is falsy, so the `if rule.pattern and argument:` branch is skipped
    assert _matches(rule, "bash", argument="")


def test_matches_no_pattern_rule_ignores_argument():
    """A rule with no pattern matches regardless of what argument is passed."""
    rule = Rule(tool="read", action=PermissionAction.ALLOW)
    assert _matches(rule, "read", argument="/etc/passwd")
    assert _matches(rule, "read", argument=None)


# ---------------------------------------------------------------------------
# PermissionService — save_rule / load_rules
# ---------------------------------------------------------------------------


def test_permission_service_save_and_load_rules(tmp_path):
    """save_rule() persists a rule that load_rules() can retrieve."""
    from openvibe.bus import EventBus
    from openvibe.db import create_database
    from openvibe.permission.permission import PermissionService
    from openvibe.project import project as _project_module

    db = create_database(tmp_path / "test.db")
    project = _project_module.get_or_create(db, tmp_path)
    bus = EventBus()
    svc = PermissionService(db, bus)

    rule = Rule(tool="bash", action=PermissionAction.ALLOW, pattern=None)
    svc.save_rule(project.id, rule)

    loaded = svc.load_rules(project.id)
    assert len(loaded) == 1
    assert loaded[0].tool == "bash"
    assert loaded[0].action == PermissionAction.ALLOW
    db.close()


def test_permission_service_load_rules_empty(tmp_path):
    """load_rules() returns empty list when no rules are stored."""
    from openvibe.bus import EventBus
    from openvibe.db import create_database
    from openvibe.permission.permission import PermissionService
    from openvibe.project import project as _project_module

    db = create_database(tmp_path / "test.db")
    project = _project_module.get_or_create(db, tmp_path)
    bus = EventBus()
    svc = PermissionService(db, bus)

    assert svc.load_rules(project.id) == []
    db.close()


def test_permission_service_save_multiple_rules(tmp_path):
    """Multiple rules for the same project are stored and returned in order."""
    from openvibe.bus import EventBus
    from openvibe.db import create_database
    from openvibe.permission.permission import PermissionService
    from openvibe.project import project as _project_module

    db = create_database(tmp_path / "test.db")
    project = _project_module.get_or_create(db, tmp_path)
    bus = EventBus()
    svc = PermissionService(db, bus)

    svc.save_rule(project.id, Rule(tool="bash", action=PermissionAction.ALLOW))
    svc.save_rule(project.id, Rule(tool="write", action=PermissionAction.DENY))

    loaded = svc.load_rules(project.id)
    assert len(loaded) == 2
    tools = [r.tool for r in loaded]
    assert "bash" in tools
    assert "write" in tools
    db.close()
