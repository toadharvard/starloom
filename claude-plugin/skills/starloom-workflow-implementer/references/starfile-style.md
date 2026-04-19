# Starfile Style Guide

How to write a `.star` workflow that a reader can understand in one pass and an operator can inspect without surprises.

The goal is **quiet code**: obvious shape, named pieces, no cleverness.

---

## 1. File layout

Every `.star` file should be read top-to-bottom as:

1. Module docstring — one sentence on purpose.
2. `PARAMS = [...]` — declared parameters (if any).
3. Module-level constants — prompt templates, lists of options, model names.
4. Small helper functions — prompt builders, glue.
5. `def main(): ...` — the control flow of the workflow.
6. `main()` — the final executing line.

Keep the sections in this order. Do not interleave helpers with constants or hide params inside `main`.

```python
"""Review each listed file and produce a single combined verdict."""

PARAMS = [
    param("files", type="list", description="Comma-separated file paths to review"),
    param("model", type="string", default="sonnet"),
]

REVIEW_RUBRIC = """\
- Correctness of logic
- Test coverage
- Naming and readability
"""

def build_review_prompt(path):
    return (
        "Review one file.\n\n"
        "Rubric:\n" + REVIEW_RUBRIC + "\n"
        "Path: " + path
    )

def main():
    reviews = parallel_map(
        lambda path: agent(build_review_prompt(path), flags="--model " + model),
        files,
    )
    output("\n\n".join(reviews))

main()
```

---

## 2. PARAMS is parsed literally

`PARAMS` is extracted by regex before the module runs. Therefore:

- Declare `PARAMS` at the module top-level, unindented.
- Make it a **literal** list of `param(...)` calls.
- Do not compute `PARAMS` from a helper, a loop, or a conditional.
- Keep each `param(...)` on its own line; readers skim this list.

Promote a value into `PARAMS` only when it is genuinely per-run variable. Do not parameterize every constant — constants belong above `main` as named values.

```python
# Good
PARAMS = [
    param("repo", type="string", description="Repository URL"),
    param("shards", type="int", default=4),
]

# Bad — constants pretending to be params
PARAMS = [
    param("review_header", type="string", default="Review:"),
    param("separator", type="string", default="---"),
]
```

---

## 3. Write prompts that inspect well

Starloom displays the **first line** of each prompt as `prompt_preview` in the trace. Operators skim these. Therefore:

- Never start a prompt with a blank line.
- First line should name the task — short, specific, stable.
- Put context, inputs, and rubrics on subsequent lines.

```python
# Good
prompt = (
    "Summarize the architecture decision record.\n\n"
    "ADR text:\n" + adr
)

# Bad — preview reads empty or misleading
prompt = "\n\n" + adr
prompt = adr + "\n\nNow summarize this."
```

For multi-paragraph prompts, prefer a helper function with a clear name over inline string surgery.

---

## 4. Helpers over cleverness

Starlark has no classes and no f-strings. Reach for plain functions and named intermediates, not one-liner tricks.

- Name a helper after what it produces: `build_review_prompt`, `format_file_list`, `parse_verdict`.
- Keep helpers short and pure. A helper that both builds a prompt and calls an agent is usually two helpers.
- Join strings with `+` or `"".join(...)`. For prompt-sized text, explicit `+` with newlines is fine.
- List and dict comprehensions exist and are fine; deeply nested ones are not. One level, one filter, readable — otherwise expand to a `for` loop with a clear variable name.

---

## 5. `call_agent` vs `agent` + `parallel_map`

- `call_agent(...)` — one agent, runs now, blocks, returns its output.
- `agent(...)` — a **spec**, does nothing on its own. Only use it inside a lambda given to `parallel_map`.
- `parallel_map(fn, items)` — runs `[fn(i) for i in items]` concurrently.

Idiomatic parallel fan-out:

```python
results = parallel_map(
    lambda item: agent(build_prompt(item), flags="--model haiku"),
    items,
)
```

Anti-patterns:

```python
# Bug: agent() is a spec, calling it alone produces no node.
summary = agent("Summarize " + doc)

# Bug: call_agent inside the lambda does not parallelize — it blocks each iteration.
results = parallel_map(
    lambda item: call_agent(build_prompt(item)),
    items,
)
```

---

## 6. Control flow without exceptions

Starlark has no `try/except`, no `raise`, no classes. Accept this and design around it.

- For expected failure conditions the workflow itself can detect, call `fail("reason")` — this ends the workflow.
- For branching on agent output, parse the output and branch with `if`/`elif`.
- For human gates, use `checkpoint("question")` and branch on its answer.

```python
verdict = call_agent("Evaluate the migration plan.\n\n" + plan)
if not verdict.startswith("PASS"):
    fail("Migration plan did not pass: " + verdict)

answer = checkpoint("Apply migration to production? (yes/no)")
if answer != "yes":
    fail("Operator declined.")
```

Do not wrap `call_agent` trying to "catch" an error — there is no catch. Agent-side errors surface as failed nodes and propagate out of the workflow.

---

## 7. `output` — print-like log channel

`output(...)` is the workflow's `print`: each call renders a separate "Output" panel in the rich attach view and emits a `workflow.output` event. There is no "final result" slot — programmatic consumers reconstruct whatever they need from the sequence of events.

Two valid patterns:

- **Single result.** One `output(...)` call at the tail of `main()`. This is the default when the workflow produces a single artifact and nothing else needs to be visible.

  ```python
  def main():
      result = call_agent(build_prompt())
      output(result)

  main()
  ```

- **Progressive blocks.** Multiple `output(...)` calls when the architecture wants intermediate results visible to operators.

  ```python
  def main():
      summary = call_agent(build_summary_prompt(doc))
      output(summary)

      verdict = call_agent(build_verdict_prompt(summary))
      output(verdict)

  main()
  ```

Do not use `output` for free-form debug noise — each call is a visible block to the operator. Emit it only when the value is something the reader should see.

---

## 8. Naming and comments

- Names do the documenting. A variable called `shard_count` does not need `# number of shards`.
- Use a comment only for **why** something is non-obvious (a hidden constraint, a runtime quirk). Do not narrate what the code does.
- No banners, no decorative separators, no section comments inside `main`.

---

## 9. Parser-safe Starlark

If the parser chokes, simplify before being clever.

- **No `while` loops.** Starloom uses `starlark-go`, which disables `while` by default — a `while` statement will fail to parse. Use `for item in items:` over a concrete list instead. This is the single most common mistake; catch it early.
- No f-strings, no walrus `:=`, no `match`, no decorators, no `yield`.
- No Python standard library: no `json`, `os`, `re`, `datetime`, etc.
- No filesystem or network access at all.
- Keep expressions short; prefer intermediate variables.

Starlark language restrictions live in `references/starlark-notes.md`.

---

## 10. Minimum viable workflow skeleton

When in doubt, start from this shape:

```python
"""<one-sentence description of the workflow>."""

PARAMS = [
    # param("input", type="string", description="..."),
]

def main():
    result = call_agent("<first line is the preview>\n\n<context>")
    output(result)

main()
```

Then add helpers, parallelism, and checkpoints only when the approved architecture calls for them.
