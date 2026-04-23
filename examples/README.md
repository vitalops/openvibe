# openvibe Examples

openvibe takes a task and executes it end-to-end using whatever tools the task
requires. Once done, it evaluates its own output by designing a simulation
environment, generating scenario datasets, running them, and producing a scored
report. It then offers to improve based on what the evaluation found.

---

## [`github_dashboard/`](./github_dashboard/)

A software engineering task solved end-to-end using openvibe. It builds a GitHub
analytics dashboard for `pallets/flask` from a plain task description, evaluates
what it built by designing a simulation environment and generating test scenarios,
and offers to improve the implementation based on the results.

---

## [`headless/`](./headless/)

Drive openvibe from Python code with no TUI — one-shot, multi-turn,
permission-gated, and structured-output patterns.

---

## Prerequisites

```bash
pip install -e ".[dev]"
pip install flask requests
openvibe   # run once to configure model and credentials
```
