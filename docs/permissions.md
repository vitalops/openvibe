# Permissions

Every tool call is checked against an ordered rule list before it executes. This gives you precise control over what the agent can do autonomously and what requires your approval.

## How rule evaluation works

1. Rules are evaluated in order — the **first matching rule wins**.
2. If no rule matches, the default is **ask**.
3. Rules use [fnmatch](https://docs.python.org/3/library/fnmatch.html) glob patterns for both tool name and argument.

**Rule evaluation order (highest to lowest priority):**

1. Session-mode rules (prepended by `mode="smart"` or `mode="bypass"`)
2. Agent-level rules (defined per built-in or custom agent)
3. Project config rules (`openvibe.json`)
4. Stored "allow always" rules (saved from interactive prompts)
5. **Default:** ask

## Permission actions

| Action | Behaviour |
|--------|-----------|
| `allow` | Proceed immediately — no prompt |
| `ask` | Suspend the tool call; show a permission prompt |
| `deny` | Raise `PermissionDenied` — the tool call is aborted |

## Permission modes

Set at session creation time to configure the baseline behaviour.

### `default`

Ask for every tool call not already covered by agent or project rules. The safest option.

```python
session = ov.create_session()              # default
session = ov.create_session(mode="default")
```

### `smart`

Pre-approves common safe operations so the agent can work without constant interruption on routine coding tasks. Still asks for anything that could be destructive or have external side effects.

```python
session = ov.create_session(mode="smart")
# TUI: Ctrl+A or click "Smart Permissions"
```

**Pre-approved in smart mode:**

| Category | Approved |
|----------|----------|
| Read tools | `read`, `glob`, `grep`, `screenshot`, `ocr`, `clipboard` |
| File editing | `write`, `edit` |
| Filesystem bash | `ls*`, `cat *`, `head *`, `tail *`, `find *`, `wc *`, `diff *`, `echo *`, `pwd` |
| Directory ops | `mkdir*`, `touch *`, `cp *`, `mv *` |
| Read-only git | `git status*`, `git log*`, `git diff*`, `git show*`, `git branch*` |
| Running code | `python*`, `python3*`, `pip*`, `uv *`, `npm *`, `node *`, `cargo *`, `go *` |

**Still asks in smart mode:** `rm`, `curl`, `wget`, `ssh`, `git push`, `git commit`, arbitrary scripts, `mouse`, `keyboard`, `app`.

### `bypass`

Auto-approves every tool call for the lifetime of this session. Use only when you fully trust the task being performed.

```python
session = ov.create_session(mode="bypass")
```

## Permission rules in config

Define project-level rules in `openvibe.json`:

```json
{
  "permission": [
    {"tool": "read",  "action": "allow"},
    {"tool": "bash",  "action": "deny",  "pattern": "rm *"},
    {"tool": "bash",  "action": "deny",  "pattern": "rm -rf *"},
    {"tool": "bash",  "action": "ask"}
  ]
}
```

Rules are evaluated in the order they are listed. Put more specific rules before broader ones.

### Rule schema

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `tool` | `str` | yes | Tool name or fnmatch glob (e.g. `"bash"`, `"*"`) |
| `action` | `str` | yes | `"allow"` \| `"ask"` \| `"deny"` |
| `pattern` | `str \| null` | no | fnmatch glob matched against the tool's argument (command string for bash, file path for write/edit) |

### Pattern matching examples

```json
{"tool": "bash", "action": "deny", "pattern": "rm *"}
```
Matches any `bash` call whose command starts with `rm `.

```json
{"tool": "write", "action": "allow", "pattern": "/tmp/*"}
```
Auto-approves writing files under `/tmp/`.

```json
{"tool": "*", "action": "allow"}
```
Wildcard — allow all tools (equivalent to bypass mode).

## Storing rules permanently

When a permission prompt appears in the TUI and you choose `"2 always"`, the allow rule is saved to the project database. It applies to all future sessions in this project.

```
/permissions              # list all rules (config + agent + stored)
/permissions reset        # clear all stored allow-always rules
```

## Interactive permission prompts (TUI)

```
Tool: bash
Action: run git commit -m "feat: add auth"

  1 allow     2 always     3 deny    (enter = 1)
```

| Choice | Key | Effect |
|--------|-----|--------|
| allow | `1` or `Enter` | Approve this one call |
| always | `2` | Approve and save as a permanent project rule |
| deny | `3` | Reject; the agent receives an error and may adjust |

## Interactive permission handling (API)

```python
from openvibe.api import SessionState

response = session.send("Delete all .pyc files")

while response.state == SessionState.WAITING:
    req = response.request
    print(f"Tool: {req.tool}")
    print(f"Command: {req.argument}")
    print(f"Description: {req.description}")
    choice = input("[allow/always/deny]: ").strip() or "allow"
    response = session.reply(req.id, choice)
```

## Agent-level default rules

Each built-in agent ships with a default ruleset that applies before project config rules:

| Agent | Write/Edit | Bash | Computer control |
|-------|-----------|------|-----------------|
| `build` | ask | ask | screenshot/ui = allow; mouse/keyboard/app = ask |
| `plan` | deny | deny | — |
| `general` | deny | deny | — |
| `computer` | ask | ask | screenshot/ui = allow; mouse/keyboard/app = ask |

These are overridden by session-mode rules (smart/bypass) and can be further adjusted with project config rules.
