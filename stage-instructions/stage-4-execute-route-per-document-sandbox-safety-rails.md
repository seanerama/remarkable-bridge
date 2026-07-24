# Stage 4: Execute route + per-document sandbox safety rails

- **Type:** feature
- **Depends on:** 1 (soft: best after 3 for handwritten instructions)

## Objectives

Add the second route: an `execute`-tagged page is treated as **instructions**, run by a
headless `claude -p` **execution agent** with write/Bash tools confined to a
per-document sandbox, and an execution-report PDF is published back. This is the
highest-risk surface, so it ships behind rails and a dark-launch flag.

## What to build

- Route wiring: `watcher` maps the `execute` tag → `AgentRunner.run(execute-job)`.
- **Execution agent** (`prompts/execute.md`): allowlist `Read Write Edit Bash Grep Glob`,
  `--add-dir workspace/<doc>/` scoping filesystem access to that sandbox and nothing
  else; system prompt forbids touching the tablet or paths outside the sandbox.
- **Sandbox lifecycle:** `workspace/<doc-name>/` created per run; artifacts (e.g.
  `fib.py`) land there.
- **Execution report:** capture the agent's stdout/result → Markdown → PDF via the
  existing `publish` path, named per `response-publish`.
- **Rails:** per-run timeout (default 600s) → "timed out" report; non-zero exit →
  "failed" report; ambiguous/low-confidence instructions (bad OCR / empty extraction)
  → **"needs clarification"** report instead of guessing. Full invocation logged to
  `logs/`. Dark-launch flag `EXECUTE_ENABLED` (default **OFF**).

## Interface contracts

- **Consumes (frozen):** `agent-invocation` (the `execute` route + allowlist are already
  specified there), `response-publish`, `bridge-state`, `tablet-document`. No new
  contract — `execute` is the second route the frozen `agent-invocation` already names.
- **Exposes:** the `execute` route; no interface change for `review`.

## Testing requirements

- Unit: allowlist for `execute` is exactly the specified set; a job with `--add-dir`
  points only at the sandbox (assert argv). Confirm no tablet-write tool is ever in the
  allowlist.
- Integration (stub runner): an `execute` job writes a file into `workspace/<doc>/` and
  a report PDF is uploaded; a simulated timeout → "timed out" report is still published.
- Safety test: an agent attempt to write outside the sandbox is rejected (path scoping).
- UI-smoke (acceptance test #2): a page saying "create a python script that prints the
  first 20 fibonacci numbers and save it as fib.py" tagged `execute`, flag ON → `fib.py`
  appears in the sandbox and an execution-report PDF lands on the tablet.

## Acceptance conditions

- [ ] Kill-switch `EXECUTE_ENABLED` (default OFF) gates the whole route.
- [ ] UI-smoke authored (acceptance test #2 above).
- [ ] Sandbox confinement enforced (`--add-dir` + prompt); no tablet writes by the agent;
      never deletes anything.
- [ ] Ambiguous instructions → "needs clarification", not a guess.
- [ ] Additive only; `review` route unchanged. Existing suite green; CI all-green.

## Pipeline test: YES
