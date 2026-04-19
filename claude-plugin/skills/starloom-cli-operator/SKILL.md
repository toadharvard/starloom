---
name: starloom-cli-operator
description: Operate Starloom sessions from the CLI — create, attach, resume, patch, inspect nodes, resolve checkpoints, and diagnose lifecycle issues. Use whenever the user runs or debugs `starloom session`, `starloom node`, or `starloom checkpoint`, picks an attach output mode, decides between dry-run vs live, or needs to classify a failure as syntax vs orchestration vs runtime vs operator-flow.
---

# starloom-cli-operator

Pick the right Starloom command, in the right order, for what the user is actually doing — and keep diagnosis grounded in session evidence, not guesses about the workflow source.

## Mental model

- One workflow run = one **session** on disk. Every other command attaches, inspects, patches, resumes, or deletes *that* session.
- Each session persists two things worth reasoning about:
  - a **saved graph** (nodes, prompts, flags, status, cost)
  - an **event log** (append-only, replayable)
  Both survive process death, so you can always reconstruct state after the fact.
- A session is **live** when its `session.sock` exists and a worker is attached. Otherwise `attach` replays from disk.
- Session resolution for every command that takes an optional id:
  1. explicit `SESSION_ID` argument (or `-s/--session`)
  2. `$STARLOOM_SESSION` env var
  3. last-used session (tracked automatically)
  So prefer omitting `-s` in follow-up commands within the same lifecycle — it just works.

## Decision map — pick a command

| Goal | Command |
| --- | --- |
| Run a workflow | `starloom session create workflow.star [-p K=V ...] [--backend claude\|pi] [--dry-run]` |
| Watch it live, or replay after it finished | `starloom session attach [SESSION_ID] [-o rich\|json\|events]` |
| Restart a stopped or errored session | `starloom session resume [SESSION_ID]` |
| List sessions | `starloom session list [--status running\|completed\|error\|stopped\|crashed]` |
| Ask a running session to halt | `starloom session stop [SESSION_ID]` |
| Delete saved session data | `starloom session delete SESSION_ID \| --all [--older-than 7d] [--confirm]` |
| See the graph (nodes, status, preview, cost) | `starloom node list [-s SESSION_ID]` |
| Halt one running node | `starloom node stop NODE_ID [-s SESSION_ID]` |
| Change a node's prompt or backend flags in the saved graph (used on next run/resume) | `starloom node patch NODE_ID [-s SESSION_ID] [--prompt "..."] [--flags "..."]` |
| Answer a workflow-authored `checkpoint("...")` pause (only way to move it forward) | `starloom checkpoint answer CHECKPOINT_ID "text" [-s SESSION_ID]` |
| Approve a backend tool-call checkpoint | `starloom checkpoint approve CHECKPOINT_ID [-s SESSION_ID]` |
| Reject a backend tool-call checkpoint | `starloom checkpoint reject CHECKPOINT_ID [-s SESSION_ID] [--reason "..."]` |
| Look up a concept, event type, or status | `starloom explain [TOPIC]` |

## Command-order playbooks

### Run a workflow end-to-end

1. `starloom session create workflow.star -p topic=AI`
2. In another terminal (or same): `starloom session attach` — no id needed, last session resolves automatically.
3. Attach ends automatically on `workflow.end`, a pending checkpoint, or a terminal replay. If it ends on a checkpoint, resolve it (see below) and re-attach.
4. After resolving any checkpoint, run `starloom node list` to confirm the next node actually started. Do not rely on attach output alone — it may be stale replay.

### Diagnose a failed or stuck session

1. `starloom session list` — confirm the status (`running`, `error`, `stopped`, `crashed`).
2. `starloom node list` — find the failing/blocking node. The row includes status, kind, a prompt preview, and, when pending, a `checkpoint_id`.
3. **Classify** the failure before touching code (see *Failure classification*).
4. For a fixable node-level issue, prefer `node patch` + `session resume` over re-creating the session.

### Fix a workflow and retry without losing progress

1. `starloom node patch NODE_ID --prompt "..."` or `--flags "--model sonnet"` — updates the saved graph only; no execution.
2. `starloom session resume` — replays from the patched node; completed upstream nodes are preserved.
3. `starloom session attach` to observe.

### Bulk cleanup

- `session delete --all` without `--older-than` erases every session on disk. Always pair with `--older-than 7d` (or similar) plus `--confirm` when scripting, and confirm with the user first.
- Completed sessions cannot be resumed. Delete them only once their cost/debug history is no longer needed.

## `session create --dry-run`

Dry-run **is a real session**. It runs the `.star` script and graph-building logic; it substitutes only the agent execution with token/cost estimation.

- It produces a session id you must inspect with `attach` / `node list` like any other.
- It **does not** prove that live agents, model flags, or tool permissions will work at runtime.
- Do not start a second diagnostic session unless the first dry-run cannot explain what you see.

## Attach output modes

| Mode | Use it when |
| --- | --- |
| `rich` (default) | A human is watching. Live workflow header plus append-only events. |
| `json` | Piping structured state into another tool. |
| `events` | Tailing or replaying raw events to debug renderer/timing behavior itself. |

Attach terminates on `workflow.end`, a pending checkpoint, or a terminal replayed state. If attach seems stuck after a checkpoint answer, that is a replay/view artifact — verify progress with `node list`, then re-attach.

## Checkpoints — two kinds, and `approve`/`reject` only fit one of them

Two kinds exist in the runtime (`CheckpointKind` in `starloom/types`):

- **`CHECKPOINT`** — a literal `checkpoint("question")` builtin in the `.star`. The workflow is waiting for a *string* to flow back into the caller. Created in `starloom/builtins/workflow.py`.
- **`TOOL_CALL`** — a backend-driven tool-call interception, emitted by a hook-enabled backend (see `starloom/hooks.py`). The backend is waiting for a yes/no verdict on a specific tool call.

The server enforces which command is valid against which kind (`validate_decision` in `starloom/checkpoint.py`), and the rules are strict:

| Checkpoint kind | `answer` | `approve` | `reject` |
| --- | --- | --- | --- |
| `CHECKPOINT` (workflow-authored) | ✅ resumes the workflow with that text | ❌ `InvalidDecision` — semantically nothing to approve, a string is required | ⚠️ accepted, but **fails the checkpoint node** (`"rejected by operator"`) — aborts the workflow path, does not advance it |
| `TOOL_CALL` (backend hook) | ❌ `InvalidDecision` | ✅ allows the tool call | ✅ denies it (optionally with `--reason`) |

Practical consequences:

- If a workflow has only `checkpoint(...)` calls, **`approve` is never the right command to move forward**. `answer` is the only way. This is by design, not a bug.
- When you call the wrong one (e.g. `approve` on a workflow checkpoint), the CLI exits non-zero with a message that names the correct command — the server rejects the mismatch and the error is surfaced, not swallowed.
- `starloom checkpoint reject` on a workflow-authored checkpoint is essentially a kill switch for that branch — use it deliberately, not as a "skip".
- `approve`/`reject` apply only when a backend-driven tool-call checkpoint is pending. If `starloom node list` shows no tool-call checkpoint pending, do not try them.

Text content of `answer` is just text — strings like `"APPROVE"` or `"REJECT: ..."` are whatever the workflow author decided to parse. No CLI-level magic.

If unsure which kind is pending: `starloom node list` + `starloom explain checkpoint`.

After resolving any checkpoint, always confirm forward progress with `starloom node list`, not just attach output.

## Failure classification — do this before rewriting code

The right command sequence follows from the class. Classifying wrong wastes time rewriting `.star` code that was never the problem.

| Class | Signs | Next step |
| --- | --- | --- |
| Parse / syntax | Starlark parser error before the session really runs; errors at commas, strings, literals | Simplify the expression, split nested literals, re-run `session create`. Do not touch lifecycle. |
| Unsupported builtin / wrong host assumption | Workflow calls a name that is not a confirmed Starloom builtin; Python-isms (imports, classes, try/except, `while`) | Rewrite around confirmed builtins: `call_agent`, `agent`, `parallel_map`, `checkpoint`, `output`, `fail`, `param`. |
| Orchestration / control-flow | Parses, but shape is wrong: sequential where parallel was intended, branch taken in the wrong place, fan-out misbehaves | Redraw the shape. Make sequential vs parallel boundaries explicit. Confirm with `node list` — do not conclude shape from attach alone. |
| Session / runtime lifecycle | Session created, but attach/resume/patch behaves unexpectedly | Use `session list` + `node list` evidence. Distinguish create vs attach vs resume vs stop. Check whether `session.sock` exists to tell live from replay. |
| Checkpoint / operator-flow | Stuck on pause; unclear which command resolves it; attach looks stale after a response | Identify the checkpoint kind (see above); use the matching command; verify forward progress with `node list`. |

Do not edit `.star` code to "fix" a class-4 or class-5 problem.

## Validation loop

When drafting or revising a workflow, do not stop at "looks plausible":

1. Read the file; sanity-check syntax.
2. `starloom session create workflow.star [...]`. Use `--dry-run` first only if useful for the kind of change you're validating.
3. `starloom session attach` and watch execution shape.
4. Anything unexpected → `starloom node list`.
5. Resolve checkpoints with the correct command for their kind; re-verify with `node list`.
6. On stop/error, classify the failure class *before* rewriting.
7. Prefer `node patch` + `session resume` over re-creating, unless the graph shape itself is wrong.

## Required response shape

When helping the user, produce:

1. A concrete command sequence to run, in order, copy-pasteable.
2. For failures: the failure classification.
3. An evidence-based reason for the next debugging step, citing what `session list`, `node list`, or attach actually shows — not assumptions about the workflow source.

## What this skill does not do

- Invent commands, flags, output modes, or builtins that are not in the reference above.
- Treat `--dry-run` as a side-effect-free syntax check.
- Rewrite `.star` before the failure class is known.
- Guess at runtime backend capabilities beyond what `starloom explain` or built-in CLI help documents. When in doubt, suggest `starloom explain <topic>` or `starloom <cmd> --help`.
