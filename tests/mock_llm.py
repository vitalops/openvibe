"""Mock LLM implementations for unit tests.

Two backends are provided:

MockLLM
    A simple str→str dict-backed mock.  Given the text of the last user
    message it returns the corresponding response text.  Good for the vast
    majority of tests.

ScriptedMockLLM
    A scripted mock that replays a fixed sequence of responses in order.
    Each entry can be plain text *or* a (tool_name, args_dict) tuple that
    triggers a tool-call response.  Used for permission-flow and doom-loop
    tests where the agent must iterate more than once.

Neither backend makes any network calls, so tests run fully offline.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any


# ---------------------------------------------------------------------------
# Low-level chunk builders
# These produce objects with the same attribute shape as litellm chunks so
# the _run_turn loop can iterate over them without modification.
# ---------------------------------------------------------------------------

def _delta(content: str | None = None, tool_calls: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _choice(delta: SimpleNamespace, finish_reason: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(delta=delta, finish_reason=finish_reason)


def _chunk(
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[_choice(_delta(content, tool_calls), finish_reason)],
        usage=usage,
    )


def _tool_call_fragment(
    index: int,
    call_id: str,
    name: str,
    arguments: str,
) -> SimpleNamespace:
    """One litellm-shaped tool-call fragment."""
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ---------------------------------------------------------------------------
# Stream factories
# ---------------------------------------------------------------------------

def text_stream(text: str) -> list[SimpleNamespace]:
    """Two chunks: one carrying the text, one carrying finish_reason."""
    return [
        _chunk(content=text),
        _chunk(finish_reason="end_turn"),
    ]


def tool_call_stream(
    tool_name: str,
    args: dict[str, Any],
    call_id: str = "call_mock_001",
) -> list[SimpleNamespace]:
    """Two chunks: tool-call fragment + finish_reason chunk."""
    tc = _tool_call_fragment(0, call_id, tool_name, json.dumps(args))
    return [
        _chunk(tool_calls=[tc]),
        _chunk(finish_reason="tool_calls"),
    ]


# ---------------------------------------------------------------------------
# MockLLM — str → str dict
# ---------------------------------------------------------------------------

class MockLLM:
    """Simple mock LLM backed by a ``responses`` dict.

    The key is the text of the **last user message** in the message list
    passed to the callable.  The value is the assistant text to return.

    If no key matches, *fallback* is returned (defaults to a descriptive
    placeholder so test failures are easy to diagnose).

    Usage::

        llm = MockLLM({"hello": "world", "what is 2+2?": "4"})
        with OpenVibe(llm=llm, ...) as ov:
            resp = ov.run("hello")
            assert resp.text == "world"
    """

    def __init__(
        self,
        responses: dict[str, str],
        fallback: str = "[MockLLM: no response configured]",
    ) -> None:
        self.responses = responses
        self.fallback = fallback

    def __call__(self, model: str, messages: list[Any], **kwargs: Any) -> list[SimpleNamespace]:
        user_text = self._last_user_text(messages)
        reply = self.responses.get(user_text, self.fallback)
        return text_stream(reply)

    @staticmethod
    def _last_user_text(messages: list[Any]) -> str:
        """Return the content of the last user-role message dict."""
        for msg in reversed(messages):
            if isinstance(msg, dict) and msg.get("role") == "user":
                content = msg.get("content", "")
                return content if isinstance(content, str) else ""
        return ""


# ---------------------------------------------------------------------------
# ScriptedMockLLM — ordered sequence of responses
# ---------------------------------------------------------------------------

class ScriptedMockLLM:
    """Mock LLM that replays a scripted sequence of responses.

    Each entry in *script* is either:

    * ``str``                — plain text response
    * ``(tool_name, args)`` — a tool-call response (triggers tool execution
                              inside the agent loop)

    When the script is exhausted the last entry is repeated, so a script of
    ``["done"]`` always returns "done" no matter how many times the LLM is
    called.

    Usage::

        # First call triggers a bash command; second call returns text.
        llm = ScriptedMockLLM([
            ("bash", {"command": "echo hello"}),
            "task complete",
        ])
    """

    def __init__(self, script: list[str | tuple[str, dict[str, Any]]]) -> None:
        if not script:
            raise ValueError("script must have at least one entry")
        self._script = list(script)
        self.call_count = 0

    def __call__(self, model: str, messages: list[Any], **kwargs: Any) -> list[SimpleNamespace]:
        idx = min(self.call_count, len(self._script) - 1)
        self.call_count += 1
        entry = self._script[idx]

        if isinstance(entry, str):
            return text_stream(entry)

        tool_name, args = entry
        return tool_call_stream(tool_name, args)
