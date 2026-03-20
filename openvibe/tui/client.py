"""Async HTTP client for the openvibe server API."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx


class OpenvibeClient:
    """Thin async wrapper around the openvibe REST + SSE API."""

    def __init__(self, base_url: str = "http://127.0.0.1:4096") -> None:
        self._http = httpx.AsyncClient(base_url=base_url, timeout=30.0)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    async def is_healthy(self) -> bool:
        try:
            r = await self._http.get("/health", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    async def create_session(self, directory: str) -> dict[str, Any]:
        r = await self._http.post("/session", json={"directory": directory})
        r.raise_for_status()
        return r.json()

    async def list_sessions(self) -> list[dict[str, Any]]:
        r = await self._http.get("/session")
        r.raise_for_status()
        return r.json()

    async def get_session(self, session_id: str) -> dict[str, Any]:
        r = await self._http.get(f"/session/{session_id}")
        r.raise_for_status()
        return r.json()

    async def get_messages(self, session_id: str) -> list[dict[str, Any]]:
        r = await self._http.get(f"/session/{session_id}/messages")
        r.raise_for_status()
        return r.json()

    # ------------------------------------------------------------------
    # Messaging (SSE)
    # ------------------------------------------------------------------

    async def send_message(
        self,
        session_id: str,
        text: str,
        agent: str = "build",
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Send a message; yield (event_type, data) pairs from the SSE stream."""
        async with self._http.stream(
            "POST",
            f"/session/{session_id}/message",
            json={"text": text, "agent": agent},
            timeout=300.0,
        ) as resp:
            resp.raise_for_status()
            async for etype, data in _iter_sse(resp):
                yield etype, data

    # ------------------------------------------------------------------
    # Permission
    # ------------------------------------------------------------------

    async def reply_permission(
        self,
        request_id: str,
        decision: str,  # "allow" | "deny"
        *,
        remember: bool = False,
        project_id: str | None = None,
        tool: str | None = None,
    ) -> None:
        r = await self._http.post(
            "/permission/reply",
            json={
                "request_id": request_id,
                "decision": decision,
                "remember": remember,
                "project_id": project_id,
                "tool": tool,
            },
        )
        r.raise_for_status()


async def _iter_sse(
    response: httpx.Response,
) -> AsyncIterator[tuple[str, dict[str, Any]]]:
    """Parse SSE lines; yield (event_type, data_dict) pairs."""
    current_event = "message"
    async for line in response.aiter_lines():
        if not line:
            current_event = "message"
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:"):
            raw = line[5:].strip()
            if raw:
                try:
                    yield current_event, json.loads(raw)
                except json.JSONDecodeError:
                    pass
