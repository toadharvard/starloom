# Starloom Primitives

This is the authoritative reference for builtins available inside a Starloom `.star` workflow. Use **only** these — do not invent additional APIs or import from Python.

All builtins are injected into the Starlark module namespace before execution. They are the complete surface area the workflow can call; there is no `import`, no standard library, no further extension mechanism.

---

## `call_agent(prompt, backend=None, flags="") -> str`

Runs one agent **immediately**. Blocks the Starlark thread until the agent finishes. Returns the agent's textual output.

- `prompt`: string. First line is used as `prompt_preview` in the trace — make it meaningful.
- `backend`: optional backend name. Omit to use the workflow's default backend.
- `flags`: optional string passed through to the backend (e.g. `"--model haiku"`).

Creates a single agent node visible in the trace, with the usual lifecycle events for start, finish, error, and cache.

Use when the next step depends on the result.

```python
summary = call_agent("Summarize the architecture doc.\n\n" + doc)
```

---

## `agent(prompt, backend=None, flags="") -> dict`

**Builds a spec dict, does NOT execute.** The spec is only meaningful when handed to `parallel_map`. Returning an `agent(...)` value anywhere else is a bug.

Signature mirrors `call_agent`. Returned dict has keys `prompt`, and optionally `backend`, `flags`.

```python
specs = [agent("Review " + f, flags="--model haiku") for f in files]
# specs must go through parallel_map
```

**Never treat `agent` and `call_agent` as interchangeable.** `agent` alone produces no nodes and no output.

---

## `parallel_map(fn, items) -> list[str]`

Applies `fn` to each item to build a list of `agent(...)` specs, runs them concurrently, and returns their outputs in input order.

- `fn(item)` must return an `agent(...)` spec dict. Do not call `call_agent` inside `fn` — that blocks per-item instead of parallelising.
- All branches appear in the trace under one parallel group.
- Sibling branches keep running even if one fails; after all finish, `parallel_map` raises a single error listing every failed branch.

```python
reviews = parallel_map(
    lambda path: agent("Review file:\n" + path, flags="--model haiku"),
    file_paths,
)
```

---

## `checkpoint(question: str) -> str`

Creates an explicit operator-facing pause. Appears as a checkpoint node in the trace. Returns the operator's answer string (or `""` if the workflow is in `dry_run`).

Use for **human-in-the-loop** decisions that the workflow author wants visible and resumable via `starloom checkpoint …`.

```python
go_ahead = checkpoint("Proceed with destructive migration? (yes/no)")
if go_ahead != "yes":
    fail("Operator declined migration.")
```

Do not use `checkpoint` as a generic prompt — it is an **operator pause**, not an agent call.

---

## `output(value: str | None) -> None`

Emits an output block from the workflow. **Print-like**: every call is independent — no implicit "final result" slot, no last-wins semantics.

- Every call renders its own "Output" panel in the rich attach view.
- Every call emits a `workflow.output` event with the value.
- There is **no programmatic final result field** on `ExecutionResult`. Callers consuming the workflow programmatically reconstruct whatever they need from the sequence of `workflow.output` events on the bus (or from the persisted session events).

Use `output(...)` the way you would use `print` in a script: one call per block you want visible, whenever you have something to surface.

```python
output("step 1: plan generated")
output(final_report)
```

Passing `None` still emits the event with a null payload.

---

## `fail(message: str) -> NoReturn`

Aborts the workflow with the given message. Use only for **terminal** conditions the workflow itself has detected — not to surface agent errors (agents' errors are surfaced by their own nodes).

```python
if not verdict.startswith("PASS"):
    fail("Verdict did not pass: " + verdict)
```

There is no try/except in Starlark — `fail` ends the workflow. Do not try to "catch" it.

---

## `param(name, type="string", default=None, description="") -> dict`

Declares one workflow parameter **inside the module-level `PARAMS = [...]` list**. Resolved values are injected as top-level bindings **and** into a `PARAMS` dict before the module runs.

- `type`: one of `"string"`, `"int"`, `"float"`, `"bool"`, `"list"`.
- `default`: omitting `default=` makes the parameter **required**. `default=None` is a valid non-required default.
- `description`: shown in CLI errors when the parameter is missing.

The runtime parses `PARAMS` with a regex that expects the literal `PARAMS = [ … ]` at **line start**. Therefore:

- Declare `PARAMS` at module top-level, unindented.
- Keep it a single literal list of `param(...)` calls.
- Do not compute `PARAMS` dynamically and do not assign it from a helper.

```python
PARAMS = [
    param("repo", type="string", description="Repository URL"),
    param("shards", type="int", default=4),
    param("dry_run", type="bool", default=False),
]

def main():
    # `repo`, `shards`, `dry_run` are now bound at module scope.
    ...
```

---

## What is NOT a builtin or language feature

If you find yourself reaching for any of the following, stop — none exist and none can be imported:

- `while` loops — **disabled** by Starloom's `starlark-go` host; use `for item in items:` over a concrete list instead. This is the most common authoring mistake.
- `print`, `log`, `emit_event`
- `try/except`, `raise`, `assert`
- `import`, `from ... import`
- `open`, any filesystem access
- `requests`, `http`, network
- `time.sleep`, `datetime`, `os`, `sys`
- classes, `class` keyword (reserved, unavailable)
- `global`, `nonlocal` (reserved, unavailable)

See `references/starlark-notes.md` for the full language restrictions.

---

## Trace at a glance

Author with the trace UI and CLI in mind. Each `call_agent` and each parallel branch becomes a visible node with a prompt preview, a result, and cost telemetry. `checkpoint` becomes a visible pause. Each `output(...)` call becomes a visible output block — think of it as `print` for workflows; programmatic consumers reconstruct from `workflow.output` events.

Design workflows so an operator running `starloom session attach`, `starloom node list`, or `starloom checkpoint` can follow the flow by scanning prompt previews and node results alone.
