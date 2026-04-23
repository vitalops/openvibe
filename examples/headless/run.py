#!/usr/bin/env python3
"""
Headless openvibe — programmatic Python API
============================================

Shows how to drive openvibe from code with no TUI, no terminal interaction.
Useful for:
  - CI/CD pipelines that want LLM-powered code analysis or generation
  - Scripts that embed openvibe as a library
  - Background workers that process tasks and return structured results

Usage:
  python run.py                     # run all three demos
  python run.py --demo one-shot     # single question, stream tokens
  python run.py --demo permission   # auto-approve permission requests

Requirements:
  pip install -e ".[dev]"

Credentials:
  Credentials are loaded from the same openvibe config the TUI uses:
    ~/.config/openvibe/openvibe.json  (written when you run `openvibe` and pick a model)

  You can also set provider env vars directly as a fallback:
    export ANTHROPIC_API_KEY=sk-ant-...   (or OPENAI_API_KEY, GEMINI_API_KEY, etc.)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# ── Add repo root to path so this script works without installing ──────────────
_repo_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_repo_root))


# ── Demo 1: One-shot headless ──────────────────────────────────────────────────
#
# The simplest way to use openvibe: open a context manager, call run(),
# get the full text back. Tokens stream to the callback in real time.

def demo_one_shot() -> None:
    from openvibe import OpenVibe

    print("=" * 60)
    print("Demo 1: One-shot headless")
    print("=" * 60)
    print()

    tokens_seen: list[str] = []

    def on_token(tok: str) -> None:
        print(tok, end="", flush=True)
        tokens_seen.append(tok)

    with OpenVibe() as ov:
        result = ov.run(
            "In one sentence, what is the primary purpose of an LLM agent?",
            on_token=on_token,
        )

    print("\n")
    print(f"  State : {result.state}")
    print(f"  Tokens: {len(tokens_seen)}")
    print(f"  Text  : {result.text[:80]}…")
    print()


# ── Demo 2: Multi-turn conversation ───────────────────────────────────────────
#
# Create a session, send multiple messages in sequence.
# Each send() blocks until the turn completes.

def demo_multi_turn() -> None:
    from openvibe import OpenVibe, SessionState

    print("=" * 60)
    print("Demo 2: Multi-turn conversation")
    print("=" * 60)
    print()

    turns = [
        "Name three common data structures in one word each.",
        "Which of the three is best for FIFO ordering? One sentence.",
        "What is the time complexity of enqueue on that structure?",
    ]

    with OpenVibe() as ov:
        session = ov.create_session()

        for i, message in enumerate(turns, 1):
            print(f"  [Turn {i}] {message}")
            print("  ", end="")

            response = session.send(
                message,
                on_token=lambda t: print(t, end="", flush=True),
            )

            if response.state == SessionState.ERROR:
                print(f"\n  Error: {response.error.message}")
                break

            print("\n")

    print()


# ── Demo 3: Handling permission requests ──────────────────────────────────────
#
# When the agent needs to run a tool that requires approval, send() returns
# with state=WAITING and a populated .request field.
# Call session.reply(request.id, choice) to unblock the agent.
#
# In a headless context you can auto-approve, log, or reject programmatically.

def demo_permission() -> None:
    from openvibe import OpenVibe, SessionState

    print("=" * 60)
    print("Demo 3: Permission-gated tool use (auto-approve)")
    print("=" * 60)
    print()

    AUTO_APPROVE = "allow"  # approve every tool call automatically
    MAX_APPROVALS = 5       # safety cap for this demo

    with OpenVibe() as ov:
        session = ov.create_session()

        print("  Prompt: list the Python files in the current directory\n")
        response = session.send(
            "List the Python files in the current directory using the file system tools.",
            on_token=lambda t: print(t, end="", flush=True),
        )

        approvals = 0
        while response.state == SessionState.WAITING and approvals < MAX_APPROVALS:
            req = response.request
            print(f"\n  [Permission] {req.description}")
            print(f"  Tool: {req.tool}  Arg: {req.argument}")
            print(f"  → Auto-approving ({AUTO_APPROVE})")
            approvals += 1
            response = session.reply(req.id, AUTO_APPROVE)

        print("\n")
        if response.state == SessionState.ERROR:
            print(f"  Error: {response.error.message}")
        else:
            print(f"  Done. State: {response.state}")
            print(f"  Response snippet: {response.text[:120]}…")

    print()


# ── Demo 4: Collect structured output ─────────────────────────────────────────
#
# Accumulate the full LLM response and post-process it.
# Useful when you need to parse JSON or extract fields programmatically.

def demo_structured_output() -> None:
    from openvibe import OpenVibe

    print("=" * 60)
    print("Demo 4: Collect and post-process structured output")
    print("=" * 60)
    print()

    prompt = (
        "Reply with a JSON object only (no prose) with three keys: "
        "\"language\", \"paradigm\", \"year_released\". "
        "Use Python as the subject."
    )

    with OpenVibe() as ov:
        result = ov.run(prompt)

    import json
    raw = result.text.strip()
    # Strip markdown fences if the model wrapped the JSON
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        print("  Parsed output:")
        for k, v in data.items():
            print(f"    {k}: {v}")
    except json.JSONDecodeError:
        print("  Raw text (model did not return pure JSON):")
        print(f"  {result.text[:200]}")

    print()


# ── Entrypoint ─────────────────────────────────────────────────────────────────

_DEMOS = {
    "one-shot": demo_one_shot,
    "multi-turn": demo_multi_turn,
    "permission": demo_permission,
    "structured": demo_structured_output,
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="openvibe headless API demo")
    p.add_argument(
        "--demo",
        choices=list(_DEMOS),
        default=None,
        help=f"Which demo to run (default: all). Choices: {', '.join(_DEMOS)}",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    # load_config() MUST run before openvibe.llm (or anything that imports litellm)
    # is imported.  litellm calls load_dotenv() at module-import time, which can
    # set stale env vars from ~/.env before _apply_provider_env() gets a chance to
    # win.  Calling load_config() here (from openvibe.config, which does NOT import
    # litellm) ensures the openvibe config values land in os.environ first.
    from openvibe.config import load_config
    load_config()

    if args.demo:
        _DEMOS[args.demo]()
    else:
        for name, fn in _DEMOS.items():
            fn()

    print("All demos complete.")


if __name__ == "__main__":
    main()
