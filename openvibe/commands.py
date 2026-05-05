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
    # Text-only follow-up: TUI starts an agent turn with this text (e.g. /learn replay)
    followup_prompt: str | None = None
    # Multimodal follow-up: TUI calls the LLM directly with these content blocks
    # and saves the result — base64 images never appear in chat logs (e.g. /learn stop)
    followup_content: list | None = None   # list[dict] content blocks
    followup_task_name: str = ""           # task name for the learn summarise worker
    followup_proc_path: str = ""           # file path to save the procedure JSON


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


# Vim-style colon commands mapped to their canonical slash-command name.
_COLON_ALIASES: dict[str, str] = {
    "q": "quit",
    "quit": "quit",
    "wq": "quit",
    "qa": "quit",
    "q!": "quit",
}


def is_command(text: str) -> bool:
    """Return True if *text* looks like a slash command or a vim colon command."""
    if text.startswith("/") and len(text) > 1 and not text.startswith("//"):
        return True
    # Accept vim-style colon commands: :q, :quit, :wq, :qa, :q!
    if text.startswith(":"):
        word = text[1:].split()[0].lower() if text[1:].split() else ""
        return word in _COLON_ALIASES
    return False


def get_command(text: str) -> tuple[str, str] | None:
    """Parse ``/name args`` or ``:alias`` and return ``(name, args)`` or None."""
    if not is_command(text):
        return None
    if text.startswith(":"):
        word = text[1:].split()[0].lower()
        canonical = _COLON_ALIASES.get(word)
        if canonical is None:
            return None
        rest = text[1 + len(word):].strip()
        return canonical, rest
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


@command("quit", "Exit openvibe")
def cmd_quit(ctx: CommandContext) -> CommandResult:
    return CommandResult(output="", quit=True)


@command("q", "Exit openvibe (:q also works)")
def cmd_q(ctx: CommandContext) -> CommandResult:
    return CommandResult(output="", quit=True)


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


@command("screenshot", "Take a screenshot and display info about the current screen")
def cmd_screenshot(ctx: CommandContext) -> CommandResult:
    """Capture the screen and show dimensions (does not embed the image in TUI)."""
    try:
        from openvibe.computer.capture import capture_screen, screen_size
    except ImportError:
        return CommandResult(
            output="[red]Computer-use extras not installed.[/red]\n"
            "[dim]Run: pip install mss pillow[/dim]"
        )

    try:
        w, h = screen_size()
        lines = [
            "[bold]Screen info[/bold]\n",
            f"  [dim]Primary monitor:[/dim] [bold]{w}×{h}[/bold] pixels",
            "\n[dim]Use the 'screenshot' tool inside a computer-use session to "
            "capture the screen and pass the image to the model.[/dim]",
        ]
        return CommandResult(output="\n".join(lines))
    except Exception as exc:
        return CommandResult(output=f"[red]Screenshot failed:[/red] {exc}", )


@command("computer", "Show computer-use session info or manage the sandbox")
def cmd_computer(ctx: CommandContext) -> CommandResult:
    """Display audit log summary for the current session's computer-use sandbox."""
    try:
        from openvibe.computer.sandbox import get_sandbox
    except ImportError:
        return CommandResult(
            output="[red]Computer-use module not available.[/red]"
        )

    sandbox = get_sandbox(ctx.session.info.id)
    lines = [
        "[bold]Computer-use sandbox[/bold]\n",
        f"  [dim]Session:[/dim]      {sandbox.session_id[:16]}…",
        f"  [dim]Actions logged:[/dim] {len(sandbox.audit_log)}",
    ]
    if sandbox.allowed_apps:
        lines.append(f"  [dim]Allowed apps:[/dim]  {', '.join(sandbox.allowed_apps)}")
    else:
        lines.append("  [dim]Allowed apps:[/dim]  (all)")
    if sandbox.screen_region:
        x, y, w, h = sandbox.screen_region
        lines.append(f"  [dim]Screen region:[/dim] x={x} y={y} w={w} h={h}")
    else:
        lines.append("  [dim]Screen region:[/dim] (full screen)")

    if sandbox.audit_log:
        lines.append("\n[bold dim]Recent actions:[/bold dim]")
        for entry in sandbox.audit_log[-10:]:
            ts = entry.timestamp
            status = "[green]ok[/green]" if entry.error is None else "[red]err[/red]"
            lines.append(
                f"  [{ts:.0f}] {status} {entry.action_type.value}  "
                f"[dim]{(entry.result or entry.error or '')[:60]}[/dim]"
            )

    return CommandResult(output="\n".join(lines))


@subcommand("computer", "reset", "Clear the computer-use audit log for this session")
def cmd_computer_reset(ctx: CommandContext) -> CommandResult:
    try:
        from openvibe.computer.sandbox import clear_sandbox, get_sandbox
    except ImportError:
        return CommandResult(output="[red]Computer-use module not available.[/red]")

    count = len(get_sandbox(ctx.session.info.id).audit_log)
    clear_sandbox(ctx.session.info.id)
    return CommandResult(
        output=f"[green]Cleared {count} computer-use audit entries.[/green]"
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


# ---------------------------------------------------------------------------
# /learn — record and replay computer tasks
# ---------------------------------------------------------------------------

# Module-level recorder state (one recording session at a time)
_active_recorder: Any = None   # LearnRecorder | None
_active_task_name: str = ""


@command("learn", "Record and replay computer tasks")
def cmd_learn(ctx: CommandContext) -> CommandResult:
    return CommandResult(
        output=(
            "[bold]learn[/bold] — record and replay computer tasks\n\n"
            "  [bold cyan]/learn start[/bold cyan] [dim]<name>[/dim]   Start recording a task\n"
            "  [bold cyan]/learn stop[/bold cyan]           Stop recording and generate procedure\n"
            "  [bold cyan]/learn replay[/bold cyan] [dim]<name>[/dim]  Replay a learned task\n"
            "  [bold cyan]/learn list[/bold cyan]           List all learned tasks\n"
        )
    )


@subcommand("learn", "start", "Start recording a task globally (mouse + keyboard + screenshots)")
def cmd_learn_start(ctx: CommandContext) -> CommandResult:
    global _active_recorder, _active_task_name

    if _active_recorder is not None:
        return CommandResult(
            output=(
                f"[yellow]Already recording '[bold]{_active_task_name}[/bold]'.[/yellow]\n"
                "[dim]Run [bold]/learn stop[/bold] first.[/dim]"
            )
        )

    task_name = ctx.args.strip().strip("'\"")
    if not task_name:
        return CommandResult(output="[red]Usage: /learn start <taskname>[/red]")

    try:
        from openvibe.learn.recorder import LearnRecorder
    except ImportError as exc:
        return CommandResult(output=f"[red]Missing dependency: {exc}[/red]")

    recorder = LearnRecorder(task_name)
    try:
        recorder.start()
    except RuntimeError as exc:
        return CommandResult(output=f"[red]{exc}[/red]")

    _active_recorder = recorder
    _active_task_name = task_name

    return CommandResult(
        output=(
            f"[green]Recording started:[/green] [bold]{task_name}[/bold]\n"
            "[dim]Capturing all mouse clicks, keyboard input, and screenshots globally.\n"
            "Run [bold]/learn stop[/bold] when finished.[/dim]"
        )
    )


@subcommand("learn", "stop", "Stop recording and generate procedure via multimodal LLM call")
def cmd_learn_stop(ctx: CommandContext) -> CommandResult:
    global _active_recorder, _active_task_name

    if _active_recorder is None:
        return CommandResult(
            output="[yellow]No active recording. Start one with [bold]/learn start <name>[/bold].[/yellow]"
        )

    from pathlib import Path as _Path

    import json as _json

    from openvibe.learn.storage import procedure_path
    from openvibe.learn.trajectory import (
        build_ax_replay_context,
        build_display_summary,
        build_summarization_content,
    )

    task_name = _active_task_name
    recorder = _active_recorder
    _active_recorder = None
    _active_task_name = ""

    trajectory = recorder.stop()

    project_dir = _Path(ctx.session.info.directory)
    proc_path = procedure_path(project_dir, task_name)
    proc_path.parent.mkdir(parents=True, exist_ok=True)

    # Save accessibility replay context immediately — this is the structural WHERE/HOW
    # data derived from the OS accessibility tree.  The LLM summary will be merged in
    # later by _start_learn_summarize.
    ax_context = build_ax_replay_context(trajectory)
    proc_path.write_text(
        _json.dumps({"task_name": task_name, "ax_context": ax_context}, indent=2, ensure_ascii=False)
    )

    # Build multimodal content blocks — images sent directly, never embedded in text
    content = build_summarization_content(trajectory, str(proc_path))
    display = build_display_summary(trajectory)

    return CommandResult(
        output=(
            f"[green]Recording stopped:[/green] [bold]{task_name}[/bold]\n"
            f"{display}\n\n"
            "[dim]Analysing with vision model — procedure will be saved automatically.[/dim]"
        ),
        followup_content=content,
        followup_task_name=task_name,
        followup_proc_path=str(proc_path),
    )


@subcommand("learn", "replay", "Replay a learned task")
def cmd_learn_replay(ctx: CommandContext) -> CommandResult:
    from pathlib import Path as _Path

    from openvibe.learn.storage import load_procedure

    # First token = task name; remaining tokens = user runtime context/instructions.
    raw = ctx.args.strip()
    parts = raw.split(None, 1)
    if not parts:
        return CommandResult(output="[red]Usage: /learn replay <taskname> [optional context][/red]")
    task_name = parts[0].strip("'\"")
    user_context = parts[1].strip() if len(parts) > 1 else ""

    project_dir = _Path(ctx.session.info.directory)
    proc = load_procedure(project_dir, task_name)

    if proc is None:
        return CommandResult(
            output=(
                f"[red]No learned procedure found for '[bold]{task_name}[/bold]'.[/red]\n"
                "[dim]Use [bold]/learn list[/bold] to see available tasks.[/dim]"
            )
        )

    procedure = proc.get("procedure", "").strip()
    description = proc.get("description", task_name)
    steps: list[str] = proc.get("steps", [])

    if not procedure:
        return CommandResult(
            output=(
                f"[red]Procedure file for '[bold]{task_name}[/bold]' is incomplete "
                "(missing 'procedure' field).[/red]\n"
                "[dim]Re-record the task with [bold]/learn start[/bold].[/dim]"
            )
        )

    ax_context = proc.get("ax_context", {})
    ax_apps = ax_context.get("apps", [])
    ax_events = ax_context.get("events", [])

    user_context_section = (
        f"Additional context from user: {user_context}\n\n"
        if user_context
        else ""
    )

    # Always include the semantic description for intent context
    intent_section = f"Task: {description}\n\n" if description else ""

    if ax_apps or ax_events:
        apps_lines = "\n".join(
            f"  - {a['name']}"
            + (f" (windows seen: {', '.join(repr(w) for w in a['windows'])})" if a.get("windows") else "")
            for a in ax_apps
        ) or "  (no app data captured)"

        events_lines = "\n".join(
            f"  {i+1}. {e['action']}"
            + (f" → {e.get('role', '')} {repr(e['title']) if e.get('title') else ''}".rstrip())
            + (f" [{e['app']}" + (f" > {e['window']}" if e.get('window') else "") + "]" if e.get('app') else "")
            + (f"  key={e['key']}" if e.get('key') else "")
            for i, e in enumerate(ax_events[:40])
        ) or "  (no interaction data captured)"

        replay_prompt = (
            f"{user_context_section}"
            f"{intent_section}"
            f"Applications used during recording:\n{apps_lines}\n\n"
            f"Recorded interactions:\n{events_lines}\n\n"
            "Reproduce these interactions autonomously:\n"
            "1. Take a screenshot to see the current screen.\n"
            "2. For each recorded application that is not currently open, open it using the app tool.\n"
            "3. Reproduce the recorded interactions in the correct windows.\n"
            "4. Do not ask the user for help — use the tools to figure it out."
        )
    else:
        # No accessibility data captured — use semantic procedure only
        replay_prompt = (
            f"{user_context_section}"
            f"{intent_section}"
            f"Procedure:\n{procedure}\n\n"
            "Reproduce this autonomously:\n"
            "1. Take a screenshot to see the current screen.\n"
            "2. Open any required applications using the app tool if not already running.\n"
            "3. Execute each step using available tools.\n"
            "4. Do not ask the user for help — use the tools to figure it out."
        )

    return CommandResult(
        output=(
            f"[green]Replaying:[/green] [bold]{task_name}[/bold]\n"
            f"[dim]{description}[/dim]"
        ),
        followup_prompt=replay_prompt,
    )


@subcommand("learn", "list", "List all learned tasks for this project")
def cmd_learn_list(ctx: CommandContext) -> CommandResult:
    from pathlib import Path as _Path

    from openvibe.learn.storage import list_procedures

    project_dir = _Path(ctx.session.info.directory)
    tasks = list_procedures(project_dir)

    if not tasks:
        return CommandResult(
            output=(
                "[dim]No learned tasks yet.\n"
                "Record one with [bold]/learn start <name>[/bold].[/dim]"
            )
        )

    lines = [f"[bold]Learned tasks[/bold] ({len(tasks)}):\n"]
    for t in tasks:
        desc = f"  [dim]{t['description']}[/dim]" if t["description"] else ""
        lines.append(f"  [bold cyan]{t['name']}[/bold cyan]{desc}")

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
