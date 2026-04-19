"""Parallel naming-consistency sweep over the repo with shared findings and term glossary."""

REPO_ROOT = "."
MODEL = "codex-lb/gpt-5.4"

FINDINGS_PATH = "/tmp/starloom_legacy_findings.txt"
ROUNDS_PATH = "/tmp/starloom_legacy_rounds.txt"
TERMS_PATH = "/tmp/starloom_legacy_terms.txt"
FINAL_REPORT_PATH = "/tmp/starloom_legacy_report.txt"

FINDINGS_AGENT_FLAGS = "--model %s --thinking high --tools bash,read,grep,glob" % MODEL
TERMS_AGENT_FLAGS = "--model %s --thinking high --tools bash,read,grep,glob" % MODEL
WRITER_FLAGS = "--model %s --thinking high --tools bash" % MODEL

SHARED_CONTEXT = """You are reviewing %s.

Primary goal:
- find inconsistent naming, competing terms for the same concept, and stale leftovers that should already have been removed
- explain what looks stale, transitional, compatibility-driven, misleading, or semantically duplicated
- focus on names, docstrings, comments, help text, prompts, examples, tests, and structural leftovers in code
- do not fix anything; collect evidence and judgment only

What to prioritize:
- the same concept named differently across files or layers
- names that still encode an old architecture or old API shape
- examples/tests/docs that preserve outdated terminology after a redesign
- migration artifacts that still live in the main repo without clear reason
- compatibility or transitional wording that suggests incomplete cleanup
- dead-feeling legacy pieces that are still present but no longer seem central

Available tools:
- Use Bash for repo traversal, append-with-dedup logic, and quick searches
- Use Read/Grep/Glob to inspect files deeply

Repo hints:
- workflows/examples live under examples/
- runtime/builtins are under starloom/
- tests often preserve historical wording and compatibility assumptions

Shared findings file: %s
Rules:
- BEFORE starting, read the shared findings file if it exists
- Other agents write there in parallel; avoid duplicating existing ideas
- Add only findings that are meaningfully distinct
- Prefer higher-signal findings over exhaustive lists
- Append via Bash with your own dedup guard
- Keep each appended item compact but informative
- Use exactly this format for each appended block:

[LEGACY] short title
WHY: why this is inconsistent, stale, legacy, or suspiciously unremoved
EVIDENCE: file paths, symbols, strings, and brief supporting context

Good findings usually explain one of these:
- concept split across multiple names
- old wording surviving after an API/design change
- legacy/example artifact that should maybe not still be treated as normal
- leftover compatibility semantics contradicting the current mental model

At the end, return only the findings you personally contributed, or "No new findings".
""" % (REPO_ROOT, FINDINGS_PATH)

FINDINGS_PROMPT = SHARED_CONTEXT + """
Task:
1. Inspect the repository for inconsistent naming and legacy leftovers.
2. Prioritize findings about:
   - competing names for the same concept
   - transitional or stale terminology
   - comments/help/examples that describe an older system shape
   - files, prompts, tests, or examples that look like migration/rewrite residue
   - legacy parts that look suspiciously unremoved or semantically orphaned
3. Look especially at:
   - docstrings and comments
   - CLI explain/help text
   - example workflows
   - prompts embedded in workflows
   - tests that preserve historical semantics
   - filenames and symbol names like legacy, compatibility, old/new, rewrite, deprecated, shim, migration, no longer
4. For every candidate, decide whether it is a real naming/legacy smell or just harmless history.
5. Append only high-signal findings to the shared file.
6. Include why the inconsistency or leftover matters architecturally, ergonomically, or conceptually.

Suggested searches:
- rg -n -i 'legacy|deprecated|compat|compatibility|migration|rewrite|old|obsolete|shim|no longer|historical|transitional' %s
- rg -n 'save|persist|write|load|read|get|run|create|resume|attach|backend|flags|model|workflow|session|node' %s
- find %s -name '*.py' -o -name '*.star' -o -name '*.md'

Important:
- Do not dump raw grep output.
- Synthesize your own judgment.
- Merge multiple files into one finding when they represent one conceptual inconsistency.
- Favor subtle but meaningful naming drift over trivial wording nits.
""" % (REPO_ROOT, REPO_ROOT, REPO_ROOT)

TERMS_PROMPT = """You are building a glossary of terms used across %s.

Goal:
- collect the main terms used in code, tests, examples, and help text
- identify where the same concept has competing names
- highlight terms that look legacy, transitional, overloaded, or semantically inconsistent

Output file: %s

Tasks:
1. Traverse Python and .star files in the repo.
2. Extract recurring project terms, API terms, workflow terms, session/runtime terms, backend terms, and review/refactoring terms.
3. Group near-synonyms and variants for the same concept.
4. Explicitly call out inconsistent naming across layers/files.
5. Mark terms that appear legacy, transitional, or suspiciously retained after redesign.
6. Write a concise glossary to %s.

Required format:
# Starloom naming and legacy-term glossary

## Core terms
- term — meaning — representative files

## Variant / competing terms
- concept: term A | term B | term C — why the variation matters — representative files

## Legacy / transitional terms
- term — why it looks legacy, transitional, or oddly retained — representative files

## Highest-risk naming inconsistencies
- concept — inconsistent terms — why this may confuse contributors — representative files

Constraints:
- Base everything on actual repo evidence.
- Keep it concise but useful.
- Prefer conceptual groupings over giant term dumps.
- Use Bash to write the exact final file.
- Also return a short summary.
""" % (REPO_ROOT, TERMS_PATH, TERMS_PATH)


ROUND_MARKER = "================================================================"


def run_findings_sweep():
    prompts = [FINDINGS_PROMPT] * 10
    return parallel_map(
        lambda prompt: agent(prompt, backend="pi", flags=FINDINGS_AGENT_FLAGS),
        prompts,
    )


def collect_terms():
    return call_agent(TERMS_PROMPT, backend="pi", flags=TERMS_AGENT_FLAGS)


def write_report(rounds, terms_summary):
    report_text = "\n\n".join([
        ROUND_MARKER,
        "SHARED FINDINGS FILE: " + FINDINGS_PATH,
        "TERMS GLOSSARY FILE: " + TERMS_PATH,
        ROUND_MARKER,
        "PARALLEL FINDINGS AGENT OUTPUTS",
        "\n\n---\n\n".join(rounds),
        ROUND_MARKER,
        "TERMS AGENT SUMMARY",
        terms_summary,
    ])

    call_agent(
        """
Use Bash only.
Write the exact text below to %s, replacing file contents.

%s
""" % (FINAL_REPORT_PATH, report_text),
        backend="pi",
        flags=WRITER_FLAGS,
    )


def main():
    rounds = run_findings_sweep()
    terms_summary = collect_terms()
    write_report(rounds, terms_summary)
    output(FINAL_REPORT_PATH)


main()
