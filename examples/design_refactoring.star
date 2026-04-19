"""Architect reviews general architecture problems and proposes fix plans."""

PROMPT = """REVIEW general architecture problems and propose fixes.
You are a software architect reviewing the current repository.

Repository context:
- Starlark workflow orchestrator
- Python codebase with CLI, UI, session persistence, orchestration, runtime, and backend integrations
- Prefer current product terminology throughout: session, persisted workflow source,
  trace graph, backend-specific raw flags, replayed event history

Your job is NOT to validate a fixed list of known examples.
Your job IS to discover the most important architecture and design problems that exist now.

Inspect the real code and identify general issues such as:
- poor module boundaries
- tight coupling across layers
- mixed responsibilities inside one file/class/module
- duplicated logic or duplicated data models
- leaky abstractions
- confused ownership of persistence, state, or I/O
- command/query or control/data concerns mixed together
- backend-specific details leaking into product-level abstractions
- stale compatibility layers or transitional architecture residue
- naming that hides architectural intent
- dead extension points, no-op protocols, or abstractions with no real callers
- places where tests/docs/code imply conflicting architecture

Guidance:
- Prefer findings that materially affect maintainability, correctness, evolvability, or conceptual clarity.
- Do not pad with tiny style nits.
- Do not assume something is wrong only because it is backend-specific; backend protocol details may validly remain in backend code.
- Distinguish between:
  1. true architectural problem,
  2. acceptable local tradeoff,
  3. external/backend constraint,
  4. already-cleaned residue that no longer matters.
- Read code before judging.
- Follow imports/call paths when needed.
- Focus on current reality, not historical assumptions.

Required output structure:

SUMMARY
- 5-10 sentence overview of the main architectural health of the repo.

TOP PROBLEMS
For each important problem, use this exact structure:

## <short problem title>
SEVERITY: BLOCKER | HIGH | MEDIUM | LOW
WHY IT MATTERS: <2-6 sentences>
EVIDENCE:
- <file/path.py>:<line or symbol> - <what is happening>
- <file/path.py>:<line or symbol> - <what is happening>

RECOMMENDED FIX:
- <concrete change>
- <concrete change>

ATOMIC STEPS:
1. <small step that should keep tests passing>
2. <next step>
3. <next step>

RISKS:
- <migration risk or compatibility concern>

NON-PROBLEMS / ACCEPTABLE TRADEOFFS
- List a few things that may look suspicious but are reasonable as currently implemented.

PRIORITIZED EXECUTION PLAN
1. <highest-value fix first>
2. <next>
3. <next>

Use Read, Grep, and Bash as needed. Output only the report.
"""


def main():
    report = call_agent(PROMPT, flags="--provider codex-lb --model gpt-5.4 --thinking xhigh --tools bash,read,grep")
    output(report)


main()
