"""CLI entry point.

Usage
-----
    openvibe                     # start the server (default)
    openvibe serve               # explicitly start the server
    openvibe run "fix the bug"   # one-shot: run a prompt and exit
    openvibe session list        # list sessions
    openvibe session show <id>   # show session messages

Environment variables
---------------------
    OPENVIBE_PORT          server port (default: 4096)
    OPENVIBE_HOST          server host (default: 127.0.0.1)
    OPENVIBE_PROJECT_DIR   override the project directory
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(
    name="openvibe",
    help="Open-source AI coding agent.",
    no_args_is_help=False,
    add_completion=False,
)
session_app = typer.Typer(name="session", help="Manage sessions.")
app.add_typer(session_app)

console = Console()
err_console = Console(stderr=True, style="red")

DEFAULT_PORT = int(os.environ.get("OPENVIBE_PORT", "4096"))
DEFAULT_HOST = os.environ.get("OPENVIBE_HOST", "127.0.0.1")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_project_dir() -> Path:
    env = os.environ.get("OPENVIBE_PROJECT_DIR")
    return Path(env).resolve() if env else Path.cwd()


def _get_server_url() -> str:
    return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"


def _require_server() -> "httpx.Client":
    """Return an httpx client pointed at a running server, or exit."""
    import httpx
    url = _get_server_url()
    try:
        client = httpx.Client(base_url=url, timeout=10)
        client.get("/health").raise_for_status()
        return client
    except Exception:
        err_console.print(
            f"[bold]openvibe server not running.[/bold] Start it with: openvibe serve\n"
            f"Expected at {url}"
        )
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@app.callback(invoke_without_command=True)
def root(
    ctx: typer.Context,
    version: bool = typer.Option(False, "--version", "-v", help="Print version and exit."),
) -> None:
    if version:
        from openvibe import __version__
        console.print(f"openvibe {__version__}")
        raise typer.Exit()
    if ctx.invoked_subcommand is None:
        from openvibe.tui.app import run_tui
        run_tui(project_dir=_get_project_dir())


@app.command()
def tui(
    project_dir: Optional[str] = typer.Option(None, "--dir", "-d", help="Project directory."),
) -> None:
    """Launch the interactive TUI."""
    from openvibe.tui.app import run_tui

    resolved_dir = Path(project_dir).resolve() if project_dir else _get_project_dir()
    run_tui(project_dir=resolved_dir)


@app.command()
def serve(
    host: str = typer.Option(DEFAULT_HOST, "--host", "-H", help="Host to bind to."),
    port: int = typer.Option(DEFAULT_PORT, "--port", "-p", help="Port to listen on."),
    project_dir: Optional[str] = typer.Option(None, "--dir", "-d", help="Project directory."),
    reload: bool = typer.Option(False, "--reload", help="Auto-reload on code changes (dev only)."),
) -> None:
    """Start the openvibe HTTP server."""
    import uvicorn

    resolved_dir = Path(project_dir).resolve() if project_dir else _get_project_dir()
    console.print(f"[bold green]openvibe[/bold green] serving [cyan]{resolved_dir}[/cyan] on {host}:{port}")

    from openvibe.config import load_config
    from openvibe.server import create_app

    config = load_config(resolved_dir)
    fastapi_app = create_app(project_dir=resolved_dir, config=config)

    uvicorn.run(
        fastapi_app,
        host=host,
        port=port,
        reload=reload,
        log_level="warning",
    )


@app.command()
def run(
    prompt: str = typer.Argument(..., help="Prompt to send to the agent."),
    agent: str = typer.Option("build", "--agent", "-a", help="Agent to use."),
    session_id: Optional[str] = typer.Option(None, "--session", "-s", help="Resume a session."),
    project_dir: Optional[str] = typer.Option(None, "--dir", "-d", help="Project directory."),
) -> None:
    """Run a one-shot prompt against the agent and print the response."""
    import httpx

    client = _require_server()

    # Create or reuse session
    if session_id:
        sid = session_id
    else:
        resp = client.post("/session", json={"agent": agent})
        resp.raise_for_status()
        sid = resp.json()["id"]

    console.print(f"[dim]Session: {sid}[/dim]")
    console.print()

    # Stream the response
    with client.stream(
        "POST",
        f"/session/{sid}/message",
        json={"text": prompt, "agent": agent},
        timeout=300,
    ) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if line.startswith("data:"):
                raw = line[5:].strip()
                if not raw:
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                # Print text deltas
                if "content" in data:
                    console.print(data["content"], end="")


@app.command()
def models() -> None:
    """List all available models."""
    client = _require_server()
    resp = client.get("/model")
    resp.raise_for_status()

    table = Table(title="Available Models", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Provider")
    table.add_column("Context")

    for m in resp.json():
        table.add_row(
            m["id"],
            m["name"],
            m["provider_id"],
            f"{m.get('context_window', 0) // 1000}K",
        )
    console.print(table)


# ---------------------------------------------------------------------------
# session sub-commands
# ---------------------------------------------------------------------------

@session_app.command("list")
def session_list() -> None:
    """List all sessions for the current project."""
    client = _require_server()
    resp = client.get("/session")
    resp.raise_for_status()

    table = Table(title="Sessions", show_header=True, header_style="bold")
    table.add_column("ID", style="cyan")
    table.add_column("Title")
    table.add_column("Updated")

    for s in resp.json():
        table.add_row(s["id"], s.get("title") or "(untitled)", s.get("updated_at", ""))

    console.print(table)


@session_app.command("show")
def session_show(session_id: str = typer.Argument(..., help="Session ID.")) -> None:
    """Show messages for a session."""
    client = _require_server()
    resp = client.get(f"/session/{session_id}/messages")
    if resp.status_code == 404:
        err_console.print(f"Session '{session_id}' not found.")
        raise typer.Exit(1)
    resp.raise_for_status()

    for msg in resp.json():
        role_style = "bold blue" if msg["role"] == "user" else "bold green"
        console.rule(f"[{role_style}]{msg['role'].upper()}[/{role_style}]")
        for part in msg.get("parts", []):
            if part.get("type") == "text":
                console.print(part.get("content", ""))
            elif part.get("type") == "tool":
                state = part.get("state", {})
                console.print(
                    f"[dim]Tool: {state.get('tool_name')} "
                    f"[{state.get('status')}][/dim]"
                )


if __name__ == "__main__":
    app()
