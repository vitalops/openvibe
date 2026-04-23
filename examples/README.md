# openvibe Examples

---

## [`github_dashboard/`](./github_dashboard/)

openvibe builds a live analytics dashboard for the `pallets/flask` GitHub
repository — fetching real data, storing it in SQLite, and serving it through
a Flask API with an HTML UI.

The evaluation starts the live server, calls every endpoint, runs SQL queries
directly against the database, and compares the results. SimHarness scenarios
are grounded in the actual numbers, not simulated ones.

```
/build-eval examples/github_dashboard/TASK.md
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
