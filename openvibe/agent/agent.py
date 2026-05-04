"""Agent definitions.

An agent is a named configuration that controls how the LLM behaves:
which model to use, what system prompt to use, which tools are available,
and what permission rules apply.

Built-in agents
---------------
- **build**   — full-access primary agent for coding tasks (default)
- **plan**    — read-only agent for analysis and planning
- **general** — read-only subagent for research and multi-step searches

Custom agents can be defined in ``openvibe.json`` under the ``agent`` key
and will override built-in defaults with the same name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from openvibe.config import AgentConfig, AgentMode, ModelRef, PermissionAction
from openvibe.permission.permission import Rule

if TYPE_CHECKING:
    from openvibe.config import Config


# ---------------------------------------------------------------------------
# Agent runtime info
# ---------------------------------------------------------------------------


@dataclass
class AgentInfo:
    """Fully resolved agent configuration used at runtime."""

    name: str
    description: str
    system_prompt: str
    model: ModelRef | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_steps: int | None = None
    mode: AgentMode = AgentMode.PRIMARY
    permission_rules: list[Rule] = field(default_factory=list)
    disabled_tools: list[str] = field(default_factory=list)
    extra_instructions: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Default system prompts
# ---------------------------------------------------------------------------

_BUILD_SYSTEM_PROMPT = """\
You are openvibe, an expert AI process agent embedded in the developer's
terminal. You have access to the file system, a bash shell, and a suite of
tools for reading, writing, and searching code.

Guidelines:
- Be concise. Prefer code over prose.
- Think step-by-step for complex tasks. Use the todo tool to track progress.
- Read files before editing them. Understand the existing patterns first.
- Prefer targeted edits (edit tool) over full rewrites (write tool).
- Run tests after making changes to verify correctness.
- Never guess at file paths — use glob or grep to locate files first.
- When in doubt, ask a clarifying question rather than guessing.

You also have desktop/computer-use tools: screenshot, ui, mouse, keyboard,
app, clipboard, ocr. Use them automatically whenever the task involves
interacting with the screen, a GUI application, or the desktop environment —
you do not need to be in any special mode to use them.
"""

_PLAN_SYSTEM_PROMPT = """\
You are openvibe in plan mode — a read-only analysis agent. You can explore
the codebase, read files, search for patterns, and answer questions, but you
MUST NOT modify any files or run shell commands that have side effects.

Provide clear, structured analysis. Use headings, bullet points, and code
blocks where appropriate.
"""

_GENERAL_SYSTEM_PROMPT = """\
You are a general-purpose research subagent. Your role is to gather
information, search code, fetch web resources, and return findings.
You do not write or modify files.
"""

_COMPUTER_SYSTEM_PROMPT = """\
You are openvibe in computer-use mode. You can see and control the desktop.

TOOL PRIORITY — always follow this order:

1. ui tool (FIRST CHOICE — no coordinates needed, most reliable)
   • Use `ui get_tree` to list clickable elements in an app by name.
   • Use `ui click` with the element title — never guess coordinates.
   • Use `ui click_menu` to trigger menu items (File → Save, etc.).
   • Use `ui type` to enter text — handles Unicode and clipboard correctly.
   • Use `ui press_key` for keys/chords (return, escape, cmd+s, etc.).
   • ui is auto-allowed — no permission prompt.

2. app tool — open, close, focus, list applications.

3. screenshot tool — take a screenshot to observe the current screen state.
   Always take one after opening an app to confirm it appeared.
   The output includes the image dimensions — note them for step 4.

4. mouse tool (LAST RESORT — only for unlabelled canvas areas)
   • Only use when `ui get_tree` shows no accessible elements for the target.
   • ALWAYS provide image_width and image_height from the screenshot output.
     This is mandatory — without them, Retina scaling causes wrong coordinates.
   • Example: mouse click x=450 y=300 image_width=1920 image_height=1200

5. keyboard tool — raw keystroke fallback when `ui type` / `ui press_key`
   cannot be used (rare).

WORKFLOW:
  app open → screenshot → ui get_tree → ui click/type → screenshot → verify

VERIFICATION:
  Every screenshot compares automatically to the previous one and reports
  what percentage of the screen changed. If you see "No visible change
  detected" after an action, the action failed — do NOT repeat it blindly.
  Instead: try ui get_tree to find the element by name, or take a fresh
  screenshot and reassess coordinates.

Never move the mouse to (0, 0) — that triggers pyautogui's failsafe abort.
"""


# ---------------------------------------------------------------------------
# Built-in permission rulesets
# ---------------------------------------------------------------------------

_A = PermissionAction  # local alias for brevity

_BUILD_RULES: list[Rule] = [
    # Allow common read tools by default
    Rule(tool="read", action=_A.ALLOW),
    Rule(tool="glob", action=_A.ALLOW),
    Rule(tool="grep", action=_A.ALLOW),
    Rule(tool="web_fetch", action=_A.ALLOW),
    Rule(tool="todo_read", action=_A.ALLOW),
    Rule(tool="todo_write", action=_A.ALLOW),
    # Ask before write operations
    Rule(tool="write", action=_A.ASK),
    Rule(tool="edit", action=_A.ASK),
    Rule(tool="bash", action=_A.ASK),
    # Computer-use tools — auto-allowed for observation; ask for control
    Rule(tool="screenshot", action=_A.ALLOW),
    Rule(tool="ui", action=_A.ALLOW),
    Rule(tool="ocr", action=_A.ALLOW),
    Rule(tool="clipboard", action=_A.ALLOW),
    Rule(tool="mouse", action=_A.ASK),
    Rule(tool="keyboard", action=_A.ASK),
    Rule(tool="app", action=_A.ASK),
]

_PLAN_RULES: list[Rule] = [
    Rule(tool="read", action=_A.ALLOW),
    Rule(tool="glob", action=_A.ALLOW),
    Rule(tool="grep", action=_A.ALLOW),
    Rule(tool="web_fetch", action=_A.ALLOW),
    # Deny all write / execute operations
    Rule(tool="write", action=_A.DENY),
    Rule(tool="edit", action=_A.DENY),
    Rule(tool="bash", action=_A.DENY),
    Rule(tool="todo_write", action=_A.DENY),
]

_GENERAL_RULES: list[Rule] = [
    Rule(tool="read", action=_A.ALLOW),
    Rule(tool="glob", action=_A.ALLOW),
    Rule(tool="grep", action=_A.ALLOW),
    Rule(tool="web_fetch", action=_A.ALLOW),
    Rule(tool="write", action=_A.DENY),
    Rule(tool="edit", action=_A.DENY),
    Rule(tool="bash", action=_A.DENY),
]

# Computer-use: screenshot + ui (accessibility) are always allowed;
# raw mouse/keyboard/app require consent (they affect the running system).
_COMPUTER_RULES: list[Rule] = [
    Rule(tool="screenshot", action=_A.ALLOW),
    Rule(tool="ui", action=_A.ALLOW),   # AppleScript accessibility — preferred over mouse
    Rule(tool="mouse", action=_A.ASK),
    Rule(tool="keyboard", action=_A.ASK),
    Rule(tool="app", action=_A.ASK),
    # Standard tools remain available
    Rule(tool="read", action=_A.ALLOW),
    Rule(tool="glob", action=_A.ALLOW),
    Rule(tool="grep", action=_A.ALLOW),
    Rule(tool="bash", action=_A.ASK),
    Rule(tool="write", action=_A.ASK),
    Rule(tool="edit", action=_A.ASK),
]


# ---------------------------------------------------------------------------
# Built-in agent definitions
# ---------------------------------------------------------------------------

_BUILTIN_AGENTS: dict[str, AgentInfo] = {
    "build": AgentInfo(
        name="build",
        description="Full-access agent for coding and development tasks.",
        system_prompt=_BUILD_SYSTEM_PROMPT,
        mode=AgentMode.PRIMARY,
        permission_rules=_BUILD_RULES,
    ),
    "plan": AgentInfo(
        name="plan",
        description="Read-only agent for code exploration and planning.",
        system_prompt=_PLAN_SYSTEM_PROMPT,
        mode=AgentMode.PRIMARY,
        permission_rules=_PLAN_RULES,
        disabled_tools=["bash", "write", "edit", "todo_write"],
    ),
    "general": AgentInfo(
        name="general",
        description="General-purpose research subagent.",
        system_prompt=_GENERAL_SYSTEM_PROMPT,
        mode=AgentMode.SUBAGENT,
        permission_rules=_GENERAL_RULES,
        disabled_tools=["bash", "write", "edit", "todo_write"],
    ),
    "computer": AgentInfo(
        name="computer",
        description=(
            "Computer-use agent: sees the screen and controls mouse/keyboard. "
            "Requires the computer-use extras (mss, pillow, pyautogui)."
        ),
        system_prompt=_COMPUTER_SYSTEM_PROMPT,
        mode=AgentMode.PRIMARY,
        permission_rules=_COMPUTER_RULES,
    ),
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def resolve(config: "Config", name: str | None = None) -> AgentInfo:
    """Return a fully resolved AgentInfo for *name* (or the default agent).

    User config overrides are merged on top of the built-in defaults.
    Always returns a fresh copy — never mutates the global builtins.
    """
    import dataclasses

    agent_name = name or config.default_agent or "build"

    # Start from built-in or create a shell — always copy to avoid mutating globals.
    builtin = _BUILTIN_AGENTS.get(agent_name)
    if builtin is not None:
        base = dataclasses.replace(builtin)
    else:
        base = AgentInfo(name=agent_name, description="", system_prompt="")

    # Apply user overrides from config
    user_cfg: AgentConfig | None = config.agent.get(agent_name)
    if user_cfg:
        base = _apply_config(base, user_cfg)

    # Apply global model override (config.model) if agent has no model set
    if base.model is None and config.model:
        base.model = config.model

    # Append global instructions
    base.extra_instructions = list(config.instructions)

    return base


def list_agents(config: "Config") -> list[AgentInfo]:
    """Return all available agents (built-in + user-defined)."""
    names = set(_BUILTIN_AGENTS) | set(config.agent)
    return [resolve(config, n) for n in sorted(names)]


def _apply_config(base: AgentInfo, cfg: AgentConfig) -> AgentInfo:
    """Return a copy of *base* with *cfg* overrides applied."""
    import dataclasses

    updates: dict[str, object] = {}

    if cfg.model:
        updates["model"] = cfg.model
    if cfg.description:
        updates["description"] = cfg.description
    if cfg.prompt:
        # Append custom prompt to the built-in system prompt
        updates["system_prompt"] = base.system_prompt + "\n\n" + cfg.prompt
    if cfg.temperature is not None:
        updates["temperature"] = cfg.temperature
    if cfg.top_p is not None:
        updates["top_p"] = cfg.top_p
    if cfg.max_steps is not None:
        updates["max_steps"] = cfg.max_steps
    if cfg.mode:
        updates["mode"] = cfg.mode

    return dataclasses.replace(base, **updates)
