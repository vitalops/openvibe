# GitHub Analytics Dashboard

This is a software engineering task solved using openvibe. The task is to build
a live analytics dashboard that pulls real data from the `pallets/flask` GitHub
repository and serves it over a web API. openvibe executes the task end-to-end,
evaluates what it built through a simulation harness it designs itself, and
offers to improve the implementation based on what the evaluation found.

---

## The Task

Fetch commits, issues, and releases from the GitHub REST API. Store them in a
local SQLite database. Serve the data through a Flask web server with JSON
endpoints and an HTML UI.

That is the entire input. No schema, no test plan, no acceptance criteria.

---

## What Happens

**Phase 1 — Build**

openvibe reads the task and creates:

```
app/fetch.py     ← calls the GitHub API, stores results in SQLite
app/server.py    ← Flask server: /api/commits, /api/issues, /api/releases, /api/stats
data/github.db   ← populated with real data from pallets/flask
SOLUTION.md      ← what was built, how to run it, known limitations
```

**Phase 2 — Evaluate**

The simulation harness reads the task description and `SOLUTION.md`, then:

- Designs an evaluation environment — what kind of agent interacts with this
  system, what tools they use, what criteria matter
- Generates a scenario dataset across difficulty levels (easy through adversarial)
- Simulates each scenario as a multi-turn interaction and scores the outcome
- Saves a full report to `eval_output/`

```
eval_output/
  github_dashboard.jsonl          ← generated scenario dataset
  github_dashboard_report.json    ← raw metrics
  github_dashboard_report.md      ← scores by difficulty and criterion,
                                     per-scenario feedback and suggestions
```

The evaluation environment, scenario dataset, and evaluation criteria are all
generated from context. Nothing is hardcoded.

**Phase 3 — Improve**

After evaluation, openvibe asks whether to use the results to improve the
implementation. If yes, it reads the report, makes targeted changes to the
lowest-scoring areas, updates `SOLUTION.md`, and re-evaluates so the
before-and-after difference is visible.

---

## Running Via TUI

```bash
openvibe
```

Type any of these:

```
build and evaluate examples/github_dashboard/TASK.md
run examples/github_dashboard/TASK.md
execute the pipeline from examples/github_dashboard/TASK.md
```

---

## Running Via Python

```bash
# Full pipeline (build → evaluate → prompt to improve)
python examples/github_dashboard/run.py

# Build only
python examples/github_dashboard/run.py --phase build

# Evaluate only (requires a prior build)
python examples/github_dashboard/run.py --phase evaluate

# Use a different model
python examples/github_dashboard/run.py --model openai/gpt-4o
```

---

## Prerequisites

```bash
pip install -e ".[dev]"
pip install flask requests
openvibe   # run once to configure your model and API credentials
```
