"""Built-in slash commands.

Slash commands are intercepted in ``Session.send()`` before the text reaches
the LLM.  Each command is a simple function that receives a ``CommandContext``
and returns a ``CommandResult``.

The ``Session`` returns the result inside a normal ``Response`` with
``state=IDLE`` and the command output in ``.text``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from openvibe.api import Session


# ---------------------------------------------------------------------------
# Context passed to every command handler
# ---------------------------------------------------------------------------


@dataclass
class CommandContext:
    """Everything a slash command needs to inspect or mutate state."""

    session: "Session"
    args: str  # everything after the command name, stripped


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """What a slash command returns."""

    output: str  # Rich markup to display
    quit: bool = False  # signal the app to exit
    clear: bool = False  # signal the screen to clear messages


# ---------------------------------------------------------------------------
# Command registry
# ---------------------------------------------------------------------------


@dataclass
class _CommandEntry:
    handler: Callable
    description: str
    subcommands: dict[str, tuple[Callable, str]] = field(default_factory=dict)


_COMMANDS: dict[str, _CommandEntry] = {}


def command(name: str, description: str):
    """Decorator to register a slash command."""

    def decorator(fn):
        _COMMANDS[name] = _CommandEntry(handler=fn, description=description)
        return fn

    return decorator


def subcommand(parent: str, name: str, description: str):
    """Decorator to register a subcommand under *parent*."""

    def decorator(fn):
        if parent not in _COMMANDS:
            raise ValueError(
                f"Parent command /{parent} must be registered before subcommands"
            )
        _COMMANDS[parent].subcommands[name] = (fn, description)
        return fn

    return decorator


def is_command(text: str) -> bool:
    """Return True if *text* looks like a slash command."""
    return text.startswith("/") and len(text) > 1 and not text.startswith("//")


def get_command(text: str) -> tuple[str, str] | None:
    """Parse ``/name args`` and return ``(name, args)`` or None."""
    if not is_command(text):
        return None
    parts = text[1:].split(None, 1)
    name = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""
    return name, args


def _fmt_subcommands(entry: _CommandEntry, name: str) -> str:
    """Format subcommand list for display."""
    lines = []
    for sub_name, (_, sub_desc) in sorted(entry.subcommands.items()):
        lines.append(
            f"  [bold cyan]/{name} {sub_name}[/bold cyan]  [dim]{sub_desc}[/dim]"
        )
    return "\n".join(lines)


def execute(name: str, ctx: CommandContext) -> CommandResult:
    """Execute a command by name.  Returns an error result for unknown commands."""
    from rich.markup import escape

    entry = _COMMANDS.get(name)
    if entry is None:
        known = ", ".join(f"/{n}" for n in sorted(_COMMANDS))
        return CommandResult(
            output=f"[red]Unknown command:[/red] /{escape(name)}\n"
            f"[dim]Available: {known}[/dim]",
        )

    # Check if the first arg is a subcommand.
    if entry.subcommands and ctx.args.strip():
        parts = ctx.args.strip().split(None, 1)
        sub_name = parts[0].lower()
        sub_entry = entry.subcommands.get(sub_name)
        if sub_entry is not None:
            sub_handler, _ = sub_entry
            sub_ctx = CommandContext(
                session=ctx.session, args=parts[1] if len(parts) > 1 else ""
            )
            return sub_handler(sub_ctx)
        # Unknown subcommand — let the main handler deal with it (it may
        # treat args as parameters, like /model does).

    return entry.handler(ctx)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _project_dir(ctx: CommandContext) -> Path:
    return Path(ctx.session.info.directory)


def _config(ctx: CommandContext):
    return ctx.session._config


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


@command("help", "Show available commands and skills")
def cmd_help(ctx: CommandContext) -> CommandResult:
    lines = ["[bold]Available commands:[/bold]\n"]
    for name in sorted(_COMMANDS):
        entry = _COMMANDS[name]
        lines.append(
            f"  [bold cyan]/{name}[/bold cyan]  [dim]{entry.description}[/dim]"
        )
        for sub_name, (_, sub_desc) in sorted(entry.subcommands.items()):
            lines.append(f"    [bold cyan]/{name} {sub_name}[/bold cyan]  [dim]{sub_desc}[/dim]")

    # Append skills section
    try:
        from rich.markup import escape

        from openvibe.skill.registry import get_registry
        skills = get_registry().user_invocable()
        if skills:
            lines.append("\n[bold]Skills[/bold] [dim](route through the LLM):[/dim]\n")
            for skill in skills:
                aliases = (
                    f"  [dim]alias: {', '.join(f'/{a}' for a in skill.aliases)}[/dim]"
                    if skill.aliases
                    else ""
                )
                hint = f" [dim]{escape(skill.argument_hint)}[/dim]" if skill.argument_hint else ""
                lines.append(
                    f"  [bold cyan]/{escape(skill.name)}[/bold cyan]{hint}"
                    f"  [dim]{escape(skill.description)}[/dim]{aliases}"
                )
    except Exception:
        pass

    return CommandResult(output="\n".join(lines))


@command("skills", "List available skills")
def cmd_skills(ctx: CommandContext) -> CommandResult:
    """Show all user-invocable skills with metadata."""
    try:
        from openvibe.skill.registry import get_registry
    except ImportError:
        return CommandResult(output="[dim]Skills system not available.[/dim]")

    skills = get_registry().user_invocable()
    if not skills:
        return CommandResult(output="[dim]No skills registered.[/dim]")

    from rich.markup import escape

    lines = ["[bold]Available skills:[/bold]\n"]
    for skill in skills:
        lines.append(f"[bold cyan]/{escape(skill.name)}[/bold cyan]")
        if skill.aliases:
            lines[-1] += f"  [dim](aliases: {', '.join(f'/{a}' for a in skill.aliases)})[/dim]"
        lines.append(f"  [dim]{escape(skill.description)}[/dim]")
        if skill.when_to_use:
            lines.append(f"  [yellow]When to use:[/yellow] [dim]{escape(skill.when_to_use)}[/dim]")
        if skill.argument_hint:
            lines.append(
                f"  [yellow]Usage:[/yellow] [dim]/{escape(skill.name)} {escape(skill.argument_hint)}[/dim]"
            )
        if skill.tags:
            lines.append(f"  [yellow]Tags:[/yellow] [dim]{escape(', '.join(skill.tags))}[/dim]")
        lines.append("")

    return CommandResult(output="\n".join(lines))


@command("clear", "Clear conversation display")
def cmd_clear(ctx: CommandContext) -> CommandResult:
    return CommandResult(output="", clear=True)


@command("cost", "Show token usage and cost for this session")
def cmd_cost(ctx: CommandContext) -> CommandResult:
    info = ctx.session.info
    lines = [f"[bold]Session cost[/bold]  [dim]{info.id[:12]}…[/dim]\n"]

    def _fmt_tokens(n: int) -> str:
        if n >= 1_000_000:
            return f"{n / 1_000_000:.2f}M"
        if n >= 1_000:
            v = n / 1_000
            return f"{int(v)}k" if v == int(v) else f"{v:.1f}k"
        return str(n)

    lines.append(f"  Input tokens:       [bold]{_fmt_tokens(info.input_tokens)}[/bold]")
    lines.append(
        f"  Output tokens:      [bold]{_fmt_tokens(info.output_tokens)}[/bold]"
    )
    if info.cache_read_tokens:
        lines.append(
            f"  Cache read tokens:  [bold]{_fmt_tokens(info.cache_read_tokens)}[/bold]"
        )
    if info.cache_write_tokens:
        lines.append(
            f"  Cache write tokens: [bold]{_fmt_tokens(info.cache_write_tokens)}[/bold]"
        )
    total_tokens = info.input_tokens + info.output_tokens
    lines.append(f"  Total tokens:       [bold]{_fmt_tokens(total_tokens)}[/bold]")
    if info.cost:
        lines.append(f"  Cost:               [bold]${info.cost:.4f}[/bold]")
    else:
        lines.append(f"  Cost:               [dim]n/a[/dim]")
    return CommandResult(output="\n".join(lines))


@command("compact", "Summarize conversation to reduce context")
def cmd_compact(ctx: CommandContext) -> CommandResult:
    # TODO: implement actual compaction (summarize via LLM and replace history)
    return CommandResult(
        output="[dim]Compaction is not yet implemented. Coming soon.[/dim]"
    )


@command("permissions", "Show or manage permission rules")
def cmd_permissions(ctx: CommandContext) -> CommandResult:
    # No args or "list" → show rules + available subcommands.
    return _permissions_list(ctx)


@subcommand("permissions", "reset", "Clear all stored permission rules")
def cmd_permissions_reset(ctx: CommandContext) -> CommandResult:
    info = ctx.session.info
    permissions_svc = ctx.session._permissions
    if not permissions_svc:
        return CommandResult(output="[dim]No permission service available.[/dim]")

    count = permissions_svc.clear_rules(info.project_id)
    if count:
        return CommandResult(
            output=f"[green]Cleared {count} stored permission rule(s).[/green]"
        )
    return CommandResult(output="[dim]No stored rules to clear.[/dim]")


@subcommand("permissions", "list", "Show all permission rules")
def cmd_permissions_list(ctx: CommandContext) -> CommandResult:
    return _permissions_list(ctx)


def _permissions_list(ctx: CommandContext) -> CommandResult:
    info = ctx.session.info
    config = _config(ctx)

    from openvibe.agent.agent import resolve

    agent_name = ctx.session._agent_name
    agent = resolve(config, agent_name)
    agent_rules = agent.permission_rules

    lines = ["[bold]Permission rules[/bold]\n"]

    if config.permission:
        lines.append("[bold dim]Project config rules:[/bold dim]")
        for r in config.permission:
            pattern = f" [dim]({r.pattern})[/dim]" if r.pattern else ""
            lines.append(f"  {r.tool}: [bold]{r.action}[/bold]{pattern}")
    else:
        lines.append("[dim]No project config rules.[/dim]")

    if agent_rules:
        lines.append(f"\n[bold dim]Agent '{agent_name}' rules:[/bold dim]")
        for r in agent_rules:
            pattern_str = f" [dim]({r.pattern})[/dim]" if r.pattern else ""
            lines.append(f"  {r.tool}: [bold]{r.action}[/bold]{pattern_str}")

    # Load stored (allow-always) rules from DB
    permissions_svc = ctx.session._permissions
    if permissions_svc:
        stored = permissions_svc.load_rules(info.project_id)
        if stored:
            lines.append("\n[bold dim]Stored rules (allow always):[/bold dim]")
            for r in stored:
                pattern = f" [dim]({r.pattern})[/dim]" if r.pattern else ""
                lines.append(f"  {r.tool}: [bold]{r.action}[/bold]{pattern}")
        else:
            lines.append("\n[dim]No stored rules.[/dim]")

    # Show available subcommands
    entry = _COMMANDS.get("permissions")
    if entry and entry.subcommands:
        lines.append(f"\n[bold dim]Subcommands:[/bold dim]")
        lines.append(_fmt_subcommands(entry, "permissions"))

    return CommandResult(output="\n".join(lines))


@command("model", "Show or switch the active model")
def cmd_model(ctx: CommandContext) -> CommandResult:
    config = _config(ctx)
    args = ctx.args.strip()

    if not args:
        # Show current model
        model = config.model
        if model:
            lines = [
                f"[bold]Current model:[/bold] {model.provider_id}/{model.model_id}"
            ]
        else:
            lines = ["[bold]Current model:[/bold] [dim]default (set by agent)[/dim]"]

        # Show configured providers
        if config.provider:
            lines.append("\n[bold dim]Configured providers:[/bold dim]")
            for pid in sorted(config.provider):
                lines.append(f"  [dim]{pid}[/dim]")

        lines.append(
            "\n[dim]Usage: /model provider/model_id [--session|--project|--global][/dim]"
        )
        return CommandResult(output="\n".join(lines))

    # Parse scope flag from the end of the args string
    scope = "session"
    for flag in ("--session", "--global", "--project"):
        if args.endswith(flag):
            scope = flag[2:]
            args = args[: -len(flag)].strip()
            break

    if not args:
        return CommandResult(output="[red]Missing model argument.[/red]")

    # Switch model: /model provider/model_id
    if "/" in args:
        provider_id, model_id = args.split("/", 1)
    else:
        # Assume current provider or default
        provider_id = config.model.provider_id if config.model else "anthropic"
        model_id = args

    from openvibe.config import (ModelRef, save_model_to_global,
                                 save_model_to_project)

    new_model = ModelRef(provider_id=provider_id, model_id=model_id)
    model_dict = {"model": {"provider_id": provider_id, "model_id": model_id}}

    if scope == "global":
        path = save_model_to_global(new_model)
        # Also apply to current session in-memory
        ctx.session.update_session_config(model_dict)
        return CommandResult(
            output=f"[green]Model switched to:[/green] {provider_id}/{model_id}\n"
            f"[dim]Saved to {path}[/dim]"
        )

    if scope == "project":
        project_dir = _project_dir(ctx)
        path = save_model_to_project(new_model, project_dir)
        # Also apply to current session in-memory
        ctx.session.update_session_config(model_dict)
        return CommandResult(
            output=f"[green]Model switched to:[/green] {provider_id}/{model_id}\n"
            f"[dim]Saved to {path}[/dim]"
        )

    # session (default) — persists to DB so model survives session resume
    ctx.session.update_session_config(model_dict)
    return CommandResult(
        output=f"[green]Model switched to:[/green] {provider_id}/{model_id}\n"
        f"[dim]Saved to session.[/dim]"
    )


@command("quit", "Exit the application")
def cmd_quit(ctx: CommandContext) -> CommandResult:
    return CommandResult(output="", quit=True)


@command("exit", "Exit the application")
def cmd_exit(ctx: CommandContext) -> CommandResult:
    return CommandResult(output="", quit=True)


@command("config", "Show current configuration")
def cmd_config(ctx: CommandContext) -> CommandResult:
    config = _config(ctx)
    project = _project_dir(ctx)

    lines = ["[bold]Current configuration[/bold]\n"]

    # Model
    if config.model:
        lines.append(
            f"  [bold dim]model:[/bold dim] {config.model.provider_id}/{config.model.model_id}"
        )
    else:
        lines.append(f"  [bold dim]model:[/bold dim] [dim]default[/dim]")

    # Default agent
    lines.append(f"  [bold dim]default_agent:[/bold dim] {config.default_agent}")

    # Providers
    if config.provider:
        lines.append(
            f"  [bold dim]providers:[/bold dim] {', '.join(sorted(config.provider))}"
        )

    # Agents
    if config.agent:
        lines.append(
            f"  [bold dim]agents:[/bold dim] {', '.join(sorted(config.agent))}"
        )

    # MCP servers
    if config.mcp:
        lines.append(
            f"  [bold dim]mcp servers:[/bold dim] {', '.join(sorted(config.mcp))}"
        )

    # Instructions
    if config.instructions:
        lines.append(
            f"  [bold dim]instructions:[/bold dim] {len(config.instructions)} fragment(s)"
        )

    # Permission rules
    if config.permission:
        lines.append(
            f"  [bold dim]permission rules:[/bold dim] {len(config.permission)}"
        )

    # Config file locations
    lines.append("\n[bold dim]Config sources:[/bold dim]")
    for candidate in [
        project / "openvibe.json",
        project / "openvibe.jsonc",
        project / ".openvibe" / "openvibe.json",
        project / ".openvibe" / "openvibe.jsonc",
    ]:
        if candidate.exists():
            lines.append(f"  [green]●[/green] {candidate}")
            break
    else:
        lines.append(f"  [dim]No project config found in {project}[/dim]")

    global_cfg = Path.home() / ".config" / "openvibe" / "openvibe.json"
    if global_cfg.exists():
        lines.append(f"  [green]●[/green] {global_cfg}")
    else:
        lines.append(f"  [dim]No global config at {global_cfg}[/dim]")

    return CommandResult(output="\n".join(lines))


@command("init", "Create or edit project openvibe.json")
def cmd_init(ctx: CommandContext) -> CommandResult:
    project = _project_dir(ctx)

    # Check existing config
    for candidate in [
        project / "openvibe.json",
        project / "openvibe.jsonc",
        project / ".openvibe" / "openvibe.json",
        project / ".openvibe" / "openvibe.jsonc",
    ]:
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            return CommandResult(
                output=f"[bold]Project config exists:[/bold] {candidate}\n\n"
                f"[dim]{content}[/dim]\n\n"
                f"[dim]Edit this file directly to change settings.[/dim]"
            )

    # Create a minimal config
    config_path = project / "openvibe.json"
    template = {
        "model": {"provider_id": "anthropic", "model_id": "claude-sonnet-4-5"},
        "permission": [
            {"tool": "bash", "action": "ask"},
            {"tool": "file.*", "action": "allow"},
        ],
    }
    config_path.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
    return CommandResult(
        output=f"[green]Created:[/green] {config_path}\n\n"
        f"[dim]{json.dumps(template, indent=2)}[/dim]\n\n"
        f"[dim]Edit this file to customize your project settings.[/dim]"
    )
