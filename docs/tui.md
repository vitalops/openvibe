# Terminal UI

## Launching

```bash
vibe                        # start in current directory
vibe /path/to/project       # start in a specific project
openvibe                    # alternative entry point
```

## Welcome screen

On first launch you see the welcome screen with recent sessions and three action buttons:

| Button | Shortcut | Description |
|--------|----------|-------------|
| New Session | `Ctrl+N` | Standard session — asks before each tool call |
| Smart Permissions | `Ctrl+A` | Pre-approves common safe operations (see [Permissions](permissions.md)) |
| All Sessions | `Ctrl+S` | Browse all past sessions |

## Key bindings

### Input

| Key | Action |
|-----|--------|
| `Enter` | Send message |
| `Ctrl+J` | Insert newline (multi-line input) |
| `↑` | Previous message in history |
| `↓` | Next message in history |

### Navigation

| Key | Action |
|-----|--------|
| `Ctrl+N` | New session |
| `Ctrl+A` | New session with Smart Permissions |
| `Ctrl+S` | Browse all sessions |
| `Ctrl+Q` | Quit |
| `:q` | Quit (vim-style, also `:quit`, `:wq`, `:qa`, `:q!`) |

### Content

| Key | Action |
|-----|--------|
| `Ctrl+Y` | Copy the focused widget to clipboard |
| `Escape` | Cancel the current agent turn |
| Click | Focus a message or tool widget |

**Copying:** click any message or tool output to focus it, then `Ctrl+Y` to copy its plain text to the clipboard. If nothing is focused, `Ctrl+Y` copies the last assistant message.

## Permission prompts

When the agent needs approval to run a tool, the input area shows three buttons:

| Button | Key | Meaning |
|--------|-----|---------|
| `1 allow` | `1` or `Enter` | Approve this one call |
| `2 always` | `2` | Approve and save permanently for this project |
| `3 deny` | `3` | Reject the call |

## Slash commands

Type these directly in the chat input. Commands execute locally and never reach the LLM.

### General

| Command | Description |
|---------|-------------|
| `/help` | List all commands and skills |
| `/skills` | List skills with descriptions and usage hints |
| `/clear` | Clear the chat display |
| `/cost` | Show token usage and estimated cost for this session |
| `/quit` or `:q` | Exit openvibe |

### Model

| Command | Description |
|---------|-------------|
| `/model` | Show the active model and configured providers |
| `/model anthropic/claude-opus-4-6` | Switch model for this session |
| `/model openai/gpt-4o --project` | Switch and save to project config |
| `/model ollama/llama3.2 --global` | Switch and save globally |

### Configuration & permissions

| Command | Description |
|---------|-------------|
| `/config` | Show current effective configuration |
| `/init` | Create or show `openvibe.json` in the project root |
| `/permissions` | List all active permission rules |
| `/permissions reset` | Clear all stored allow-always rules |

### Computer use

| Command | Description |
|---------|-------------|
| `/screenshot` | Show primary screen dimensions |
| `/computer` | Show computer-use audit log for this session |
| `/computer reset` | Clear the computer-use audit log |

### Learn & Replay

| Command | Description |
|---------|-------------|
| `/learn` | Show learn subcommand reference |
| `/learn start <name>` | Start recording a task |
| `/learn stop` | Stop recording and generate a procedure |
| `/learn replay <name>` | Replay a learned task autonomously |
| `/learn replay <name> <context>` | Replay with additional runtime context |
| `/learn list` | List all learned tasks for this project |

## Session list screen

Press `Ctrl+S` to open the session browser. Select any session to resume it. Interrupted sessions (those with an unanswered permission prompt) resume the permission prompt automatically.

## Status bar

The status row at the bottom of the input area shows:
- Idle: keyboard shortcut hints
- Thinking: a spinner with elapsed time
- Permission: the three permission buttons
