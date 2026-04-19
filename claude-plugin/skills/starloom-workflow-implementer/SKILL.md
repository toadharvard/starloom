---
name: starloom-workflow-implementer
description: Write a clean Starloom `.star` workflow file from an already-approved architecture. Use this when the Definition of Done, control flow, and trust boundaries are already decided and the remaining job is faithful, readable implementation in Starlark. Do not use this to design a workflow, negotiate requirements, or decide what it should do.
---

# Starloom Workflow Implementer

A narrow specialist: given an approved workflow architecture, produce a clean, readable, operationally inspectable `.star` file. Nothing else.

## When to use this skill

Use this skill only when **all** of the following hold:

- The user has already described what the workflow should do and how — inputs, outputs, steps, any human gates.
- The remaining job is turning that description into valid Starlark using Starloom primitives.
- The user wants the file to be readable and inspectable, not clever.

## When to NOT use this skill

Stop and defer if the user is actually asking to:

- Decide what the workflow should accomplish.
- Pick between architectural options (sequential vs. parallel, with/without checkpoints, one agent vs. fan-out).
- Negotiate success criteria.
- Debug an existing workflow's runtime behavior — that is CLI/operator work; see `../starloom-cli-operator/SKILL.md`.

If the architecture is missing or ambiguous, **ask one focused question** about the missing piece. Do not invent the answer.

## Preconditions for implementation

Before writing Starlark, you must have:

1. **Purpose** — one sentence: what the workflow produces.
2. **Parameters** — names, types, which are required, which have defaults.
3. **Control flow** — the ordered steps, which run sequentially, which fan out, where operator checkpoints live.
4. **What gets surfaced via `output(...)`** — which values the workflow should emit as visible blocks.

If any of these four are missing, request the missing one and stop.

## Load these before writing

Read these references in this order, every time you author or revise a `.star` file:

1. `references/primitives.md` — exact signatures and semantics of every Starloom builtin.
2. `references/starfile-style.md` — file structure, prompt authoring, idioms to prefer and avoid.
3. `references/starlark-notes.md` — language restrictions you must respect.

These three files are authoritative. Do not assume anything about Starloom that is not stated in them.

## Hard rules

These are non-negotiable. Violating any of them produces broken or misleading workflows.

- **Use only confirmed builtins.** The full list lives in `references/primitives.md`. Do not invent new ones.
- **`agent(...)` is a spec, not a call.** Only pass it to `parallel_map`. Use `call_agent(...)` when you want immediate execution.
- **`PARAMS` is parsed by regex.** Declare it once, at module top-level, as a literal list of `param(...)` calls.
- **First line of every prompt is the preview.** Make it a short, specific task name and do not start prompts with a blank line.
- **`output(...)` is print-like.** Every call emits a visible block; there is no programmatic final-result slot. Emit only what the reader should see.
- **No `while`.** Starloom's `starlark-go` host disables `while` loops — use `for item in items:` over a concrete list. This is the most common authoring mistake.
- **No Python escapes.** No classes, no `try/except`, no `import`, no standard library, no f-strings.
- **No silent redesign.** If faithful implementation of the approved architecture is impossible, stop and say so — do not quietly change the design.

## What to produce

A single `.star` file that a reader can understand in one top-to-bottom pass:

1. One-line module docstring.
2. `PARAMS = [...]` if the workflow takes parameters.
3. Named constants (prompt templates, rubrics, backend/model names).
4. Small helper functions (prompt builders).
5. `def main():` holding the control flow.
6. `main()` on the final line.

Keep the file quiet. Prefer clear names over comments. Prefer helper functions over inline cleverness. Prefer explicit intermediate variables over deep expressions. If the parser gets fragile, simplify.

## Output format

When returning a result to the user, structure it as:

### Workflow

The complete `.star` file in a single fenced code block.

### Implementation notes

2–5 short bullets covering only the **non-obvious** encoding choices: why a value was parameterized, why a branch uses `call_agent` instead of `parallel_map`, why multiple `output(...)` calls were used. Skip this section when there is nothing non-obvious to say.

### Open questions

Any place where the supplied architecture was ambiguous and you made a choice to keep moving. List each as: *"I assumed X. Confirm or correct."* Omit the section if there are none.

No other sections. No summary of what you did. No pep talk.

## Worked skeleton

When a user hands over an architecture and you are ready to write, the file should look like this in shape:

```python
"""<one-sentence purpose>."""

PARAMS = [
    param("<name>", type="<type>", description="<why the user needs to supply this>"),
]

<NAMED_CONSTANT> = """\
<multiline template if useful>
"""

def build_<thing>_prompt(<inputs>):
    return (
        "<short, specific first line — this is the preview>\n\n"
        "<context>\n" + <input>
    )

def main():
    <result> = call_agent(build_<thing>_prompt(<input>))
    output(<result>)

main()
```

Grow this skeleton only as the approved architecture requires. Add `parallel_map` when there is fan-out. Add `checkpoint` only where the architecture specifies a human gate. Add `fail` only for workflow-detected terminal conditions.

## Revising an existing workflow

When asked to revise instead of author:

- Read the existing file in full before touching it.
- Change the minimum needed to satisfy the revised architecture.
- Preserve existing prompt first-lines unless the architecture requires new previews.
- Do not refactor style "while you're in there" unless the user asked.

## Failure modes to watch for

- Emitting a `.star` that uses a builtin name not in `references/primitives.md`.
- Using `agent(...)` standalone and expecting output.
- Computing `PARAMS` dynamically or declaring it inside a function — it will not be parsed.
- Starting a prompt with `"\n"` or empty whitespace — the preview will be useless.
- Writing `while`, `try:`, or `class` — the `starlark-go` host will refuse.
- Treating `output(...)` as setting a programmatic final result — it is print-like; every call is just a visible block.
- Quietly redesigning the architecture because the encoding felt awkward. Surface the awkwardness instead.
