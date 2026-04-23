# openvibe Examples

openvibe takes a task and executes it end-to-end using whatever tools the task
requires. Once done, it evaluates its own output by designing a simulation
environment, generating scenario datasets, running them, and producing a scored
report. It then offers to improve based on what the evaluation found.

---

## [`github_dashboard/`](./github_dashboard/)

openvibe builds a GitHub analytics dashboard for `pallets/flask` from a plain
task description — fetching real data, storing it in SQLite, and serving it
through a Flask API with an HTML UI. It then evaluates what it built by designing
a simulation environment, generating test scenarios, and scoring the outcome.
After evaluation it offers to improve the implementation based on the results.

```
build and evaluate examples/github_dashboard/TASK.md
```

---

## [`headless/`](./headless/)

Drive openvibe from Python code with no TUI — one-shot, multi-turn,
permission-gated, and structured-output patterns.

```bash
python examples/headless/run.py
```

---

## Prerequisites

```bash
pip install -e ".[dev]"
pip install flask requests
openvibe   # run once to configure model and credentials
```

Set `GITHUB_TOKEN` to avoid GitHub's unauthenticated rate limit (60 req/hr):

```bash
export GITHUB_TOKEN=ghp_...
```
