# Installation

## Requirements

- Python 3.11+
- An API key for at least one supported LLM provider (see [Providers](providers.md))

## Install

```bash
pip install openvibe
```

## Optional extras

### Learn & Replay

Enables global mouse/keyboard recording and macOS Accessibility tree capture for the [Learn & Replay](learn.md) feature.

```bash
pip install "openvibe[learn]"
```

Dependencies added:
- `pynput` — global mouse and keyboard listener
- `atomacos` — macOS Accessibility API (macOS only)

## API keys

Set your provider key as an environment variable before launching:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-..."
```

Or configure them in `openvibe.json` using `${VAR}` expansion (see [Configuration](configuration.md)).

## First run

```bash
vibe
# or
openvibe
```

This opens the terminal UI in the current directory. For headless use, see [API](api.md).

## Quick initialise a project

Inside any project directory, run:

```
/init
```

This creates a minimal `openvibe.json` config file you can customise.
