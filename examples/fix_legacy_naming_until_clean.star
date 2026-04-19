"""Plan, rewrite, verify, and automatically retry until naming inconsistencies and stale leftovers are removed."""

REPO_ROOT = "~/ws/starloom2"
MODEL = "codex-lb/gpt-5.4"

FINDINGS_PATH = "/tmp/starloom_legacy_findings.txt"
TERMS_PATH = "/tmp/starloom_legacy_terms.txt"
PLAN_PATH = "/tmp/starloom_legacy_cleanup_plan.txt"
POLICY_PATH = "/tmp/starloom_legacy_cleanup_policy.txt"
EXECUTION_NOTES_PATH = "/tmp/starloom_legacy_cleanup_execution.txt"
VERIFICATION_PATH = "/tmp/starloom_legacy_cleanup_verification.txt"
FINAL_REPORT_PATH = "/tmp/starloom_legacy_cleanup_final_report.txt"

PLANNER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
POLICY_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
IMPLEMENTER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,edit,write" % MODEL
INTEGRATOR_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,edit,write" % MODEL
VERIFIER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
JUDGE_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
AUDITOR_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
WRITER_FLAGS = "--model %s --thinking xhigh --tools bash" % MODEL

MAX_ROUNDS = 6
ROUND_MARKER = "================================================================"

SHARED_CONTEXT = """You are modifying %s.

Mission:
- remove naming inconsistency, transitional terminology, compatibility residue, and stale leftovers from the codebase
- prefer a single clear term per concept
- remove or rename old leftovers when they no longer belong in the current architecture
- make examples, docs, tests, CLI output, UI rendering, and code tell the same story

Non-negotiable quality bar:
- no half-finished migration language left behind if it can be removed cleanly
- no duplicate names for the same concept unless there is a strong architectural reason
- no examples that preserve obsolete mental models as if they were canonical
- no docs/help text that say a concept is legacy while the code still treats it as central, unless the design explicitly requires that split
- workflow behavior must be validated end-to-end, not only by static reading
- CLI and UI behavior must be verified with concrete output expectations
- internal persisted state must be checked where naming cleanup affects sessions, graphs, events, or snapshots

Architectural principles:
- Clean Architecture: dependencies point inward, policy separated from detail
- Strong naming cohesion: one concept, one primary name
- Remove compatibility shims unless truly necessary
- Prefer explicit current concepts over migration-era aliases
- If a compatibility layer must remain, name and document it as intentional, minimal, and non-confusing
- Tests, examples, docs, CLI, UI, and implementation must agree semantically

Scope of problems to eliminate:
- graph vs graph_pkg style naming splits
- call_agent vs agent semantic confusion if it is avoidable
- replay/hydration naming drift
- workflow/session/run/execution ambiguity where the current code can be made clearer
- old first-class agent option language surviving after the backend+flags redesign
- backend-internal transport terms leaking into public help or examples
- duplicated migration examples that should not remain as canonical examples
- stale tokenizer/helper parameter names from older API shapes
- inconsistent persistence verbs like save/persist/write and load/read/resolve/replay when a clearer vocabulary is possible
- CLI/UI text that still reflects removed concepts or stale terminology
- persisted meta/config/source/graph/event naming that no longer matches the current model
- backend-specific behavior drift between pi and claude, including claude hook/checkpoint flows

Execution requirements for every serious cleanup round:
- run targeted tests for changed areas
- run a broad repo test pass
- run real workflow scenarios, not just unit tests or a single smoke workflow
- cover the major workflow-authoring scenarios supported by the product
- verify both backend=\"pi\" and backend=\"claude\" behavior where supported by the repo
- verify claude hook/checkpoint behavior where relevant
- verify CLI commands and expected output text where affected
- verify UI/session rendering behavior where affected
- inspect persisted session artifacts when naming/storage semantics change

Use Bash/Read/Grep/Glob/Edit/Write as needed.
Avoid unrelated refactors.
""" % REPO_ROOT

PLANNER_PROMPT = "PLAN cleanup strategy.\n" + SHARED_CONTEXT + """
You are the planner.

Inputs:
- Findings file: %s
- Terms glossary: %s

Tasks:
1. Read both input files if they exist.
2. Inspect the current repo and normalize the real cleanup targets.
3. Produce a concrete cleanup plan that removes the highest-value inconsistencies rather than documenting them.
4. Split implementation into 3 streams that can run mostly independently.
5. For each stream, define:
   - exact cleanup goal
   - target files
   - required renames/removals
   - constraints
   - targeted tests to run
6. Define mandatory end-to-end verification after each round, including:
   - broad test pass
   - workflow execution checks covering the main workflow-authoring scenarios
   - backend coverage for both pi and claude where supported
   - claude hook/checkpoint coverage where relevant
   - CLI checks with expected output
   - UI/session rendering checks
   - persisted state inspection
7. Write the plan to %s.

Required plan sections:
- Executive summary
- Current inconsistencies to eliminate
- Stream 1
- Stream 2
- Stream 3
- Mandatory verification contract
- Round acceptance criteria
- Final acceptance criteria

Return a concise summary.
""" % (FINDINGS_PATH, TERMS_PATH, PLAN_PATH)

def build_implementer_prompt(stream_id, round_id):
    return ("IMPLEMENT stream %s cleanup changes.\n" % stream_id) + SHARED_CONTEXT + """
You are implementation stream %s.

Inputs:
- Plan file: %s
- Findings file: %s
- Terms glossary: %s
- Prior execution notes: %s (may or may not exist)

Tasks:
1. Read the plan and execute ONLY the work assigned to stream %s.
2. Make real code changes in the repo.
3. Remove inconsistencies instead of merely commenting around them.
4. Update tests/examples/help/docs/CLI strings/UI wording together when a concept is renamed or removed.
5. When your changes affect workflow semantics or authoring vocabulary, ensure the repo still supports the main workflow-writing scenarios, not just the one you touched.
6. Preserve backend behavior parity where intended, especially backend=\"claude\" and hook/checkpoint flows.
7. Run targeted tests for what you changed.
8. Append execution notes to %s in this exact format:

[ROUND %s][STREAM %s]
- changed: ...
- removals/renames: ...
- tests: ...
- cli/ui/workflow checks: ...
- unresolved: ...

Rules:
- Prefer deletion over compatibility shims when safe.
- If a compatibility layer must remain, minimize and justify it.
- Do not write the final report.
- Return a concise summary.
"""

def build_verifier_prompt(name, focus, round_id):
    return ("VERIFY %s issues for round %s.\n" % (name, round_id)) + SHARED_CONTEXT + """
You are verifier %s for cleanup round %s.

Inputs:
- Plan file: %s
- Execution notes: %s (if present)
- Findings file: %s
- Terms glossary: %s

Your job is to judge the CURRENT code only.

Focus area:
%s

Tasks:
1. Inspect the repo after the current implementation round.
2. Identify only real remaining problems.
3. Ignore already-fixed issues.
4. Be strict about naming consistency, leftover migration residue, stale examples, contradictions between docs/tests/code, end-to-end behavioral mismatches, and backend-specific drift between pi and claude.
5. Write your result as one of:
   - CLEAN
   - RETRY_REQUIRED\n<bulleted concrete remaining issues>
6. Keep remaining issues actionable and specific.

Return only the verdict and concrete issues.
"""

def build_auditor_prompt(round_id):
    return (("AUDIT end-to-end behavior for round %s.\n" % round_id) + SHARED_CONTEXT + """
You are the end-to-end auditor for cleanup round %s.

Inputs:
- Plan file: %s
- Execution notes: %s

Your job is to verify the real running system after the implementation/integration work.

Mandatory tasks:
1. Run a broad repo test pass appropriate for the current changes.
2. Run real workflow executions covering the main supported workflow-authoring scenarios, not just one smoke case.
3. The workflow scenario coverage must include as many of these as relevant and available in the repo:
   - simple single-agent workflow
   - parallel_map workflow
   - backend="pi" usage
   - backend="claude" usage
   - claude hook/checkpoint behavior where the repo supports testing it
   - params/param(...) usage
   - output(...) behavior
   - checkpoint(...) behavior and checkpoint CLI interactions
   - session create/resume/attach flows
   - node listing / node patch or stop flows where relevant
   - replay/attach/snapshot behavior for persisted sessions
4. Check CLI behavior and concrete output text for affected commands/help/explain/listing flows.
5. Check UI/session rendering behavior for affected paths, including attach/replay/snapshot-style flows if relevant.
6. Inspect persisted state on disk where appropriate:
   - meta.json
   - config.json
   - workflow.star
   - graph.json
   - events.jsonl
7. Validate internal state and external output align with the cleaned-up naming model.
8. If something is still inconsistent, stale, misleading, broken, or only partially renamed, fail the round.

Required output format:
- CLEAN
or
- RETRY_REQUIRED
  - exact failing expectation
  - exact command/test/state that proves it
  - exact files/concepts likely needing another round

Be concrete. Prefer executable evidence over vague critique.
""" % (round_id, PLAN_PATH, EXECUTION_NOTES_PATH))

def build_integrator_prompt(round_id):
    return ("INTEGRATE cleanup round %s.\n" % round_id) + SHARED_CONTEXT + """
You are the integrator for cleanup round %s.

Inputs:
- Plan file: %s
- Execution notes: %s

Tasks:
1. Inspect the repo after the 3 implementation streams.
2. Resolve collisions, partial renames, inconsistent follow-through, broken tests, CLI text mismatches, UI/state mismatches, workflow-scenario regressions, and backend-specific regressions.
3. Run a broader practical test pass before handing off to verifiers.
4. Ensure the repo still supports the main workflow-writing scenarios after cleanup, including claude backend and hook/checkpoint paths where supported.
5. Append an integration note to %s in this format:

[ROUND %s][INTEGRATION]
- integrated: ...
- cleanups completed: ...
- tests: ...
- cli/ui/workflow/state checks: ...
- remaining risks: ...

Return a concise summary.
"""

POLICY_PROMPT = "SET cleanup policy and blocker rules.\n" + SHARED_CONTEXT + """
You are the cleanup policy author.

Inputs:
- Plan file: %s
- Findings file: %s
- Terms glossary: %s

Tasks:
1. Read the plan and repo context.
2. Define a short policy that decides what counts as:
   - BLOCKER
   - WARNING
   - EXTERNAL
   - NON_GOAL
3. Explicitly classify provider-specific backend API details and compatibility decisions when they are acceptable.
4. Write the policy to %s.

Required sections:
- BLOCKER RULES
- WARNING RULES
- EXTERNAL RULES
- NON-GOALS
- AGGREGATION RULE

Keep it concrete and short.
Return a concise summary.
""" % (PLAN_PATH, FINDINGS_PATH, TERMS_PATH, POLICY_PATH)

def build_judge_prompt(round_id, integration_summary, verifier_results, auditor_result, exhausted):
    prompt = ("NORMALIZE verifier outputs for round %s.\n" % round_id) + SHARED_CONTEXT + """
You are the cleanup judge for round %s.

Inputs:
- Policy file: %s
- Plan file: %s
- Execution notes: %s
- Integration summary: %s
- Auditor result: %s
- Verifier results:
%s

Tasks:
1. Read the policy and apply it strictly.
2. Deduplicate overlapping findings.
3. Ignore NON_GOALS.
4. Classify remaining issues into BLOCKER, WARNING, or EXTERNAL.
5. Return exactly one final verdict block in this format:

VERDICT: CLEAN
or
VERDICT: RETRY_REQUIRED
or
VERDICT: MAX_ROUNDS_EXHAUSTED

BLOCKERS:
- ...

WARNINGS:
- ...

EXTERNAL:
- ...

Rules:
- Only BLOCKERS trigger retry.
- WARNING and EXTERNAL items must not trigger retry.
- If no BLOCKERS remain, return VERDICT: CLEAN.
- If BLOCKERS remain and more rounds are available, return VERDICT: RETRY_REQUIRED.
- If BLOCKERS remain and no rounds are available, return VERDICT: MAX_ROUNDS_EXHAUSTED.
"""
    if exhausted:
        prompt = prompt + "\n\nThe round limit is exhausted for this workflow."
    return prompt

VERIFIER_SPECS = [
    (
        "naming",
        "Naming consistency across core concepts, modules, helpers, persisted artifacts, and examples. Look for one concept carrying multiple competing names or stale aliases surviving after renames.",
    ),
    (
        "legacy-residue",
        "Legacy leftovers that should have been removed: compatibility facades, migration residue, duplicate examples, stale comments/docstrings, transitional helper parameters, dead-feeling files.",
    ),
    (
        "semantic-alignment",
        "Consistency between docs, help text, examples, tests, CLI behavior, UI behavior, persisted state, implementation, and backend-specific behavior. Look for places where one layer says a concept is gone/legacy but another still treats it as first-class.",
    ),
]


def plan():
    return call_agent(PLANNER_PROMPT, backend="pi", flags=PLANNER_FLAGS)


def write_policy():
    return call_agent(POLICY_PROMPT, backend="pi", flags=POLICY_FLAGS)


def implementation_stream(stream_id, round_id):
    prompt = build_implementer_prompt(stream_id, round_id) % (
        stream_id,
        PLAN_PATH,
        FINDINGS_PATH,
        TERMS_PATH,
        EXECUTION_NOTES_PATH,
        stream_id,
        EXECUTION_NOTES_PATH,
        round_id,
        stream_id,
    )
    return agent(prompt, backend="pi", flags=IMPLEMENTER_FLAGS)


def run_implementation_round(round_id):
    return parallel_map(
        lambda stream_id: implementation_stream(stream_id, round_id),
        ["1", "2", "3"],
    )


def integrate(round_id):
    prompt = build_integrator_prompt(round_id) % (
        round_id,
        PLAN_PATH,
        EXECUTION_NOTES_PATH,
        EXECUTION_NOTES_PATH,
        round_id,
    )
    return call_agent(prompt, backend="pi", flags=INTEGRATOR_FLAGS)


def verify_one(spec, round_id):
    name = spec[0]
    focus = spec[1]
    prompt = build_verifier_prompt(name, focus, round_id) % (
        name,
        round_id,
        PLAN_PATH,
        EXECUTION_NOTES_PATH,
        FINDINGS_PATH,
        TERMS_PATH,
        focus,
    )
    return agent(prompt, backend="pi", flags=VERIFIER_FLAGS)


def verify_round(round_id):
    return parallel_map(
        lambda spec: verify_one(spec, round_id),
        VERIFIER_SPECS,
    )


def audit_round(round_id):
    prompt = build_auditor_prompt(round_id)
    return call_agent(prompt, backend="pi", flags=AUDITOR_FLAGS)


def judge_round(round_id, integration_summary, verifier_results, auditor_result, exhausted):
    prompt = build_judge_prompt(round_id, integration_summary, verifier_results, auditor_result, exhausted) % (
        round_id,
        POLICY_PATH,
        PLAN_PATH,
        EXECUTION_NOTES_PATH,
        integration_summary,
        auditor_result,
        "\n\n---\n\n".join(verifier_results),
    )
    return call_agent(prompt, backend="pi", flags=JUDGE_FLAGS)


def verdict_has_blockers(judge_result):
    return "VERDICT: RETRY_REQUIRED" in judge_result or "VERDICT: MAX_ROUNDS_EXHAUSTED" in judge_result


def verdict_is_exhausted(judge_result):
    return "VERDICT: MAX_ROUNDS_EXHAUSTED" in judge_result


def write_verification_log(round_id, verifier_results, integration_summary, auditor_result, judge_result):
    text = "\n\n".join([
        ROUND_MARKER,
        "ROUND %s" % round_id,
        "INTEGRATION SUMMARY",
        integration_summary,
        "AUDITOR RESULT",
        auditor_result,
        "VERIFIER RESULTS",
        "\n\n---\n\n".join(verifier_results),
        "JUDGE RESULT",
        judge_result,
    ])
    call_agent(
        """
Use Bash only.
Append the exact text below to %s.

%s
""" % (VERIFICATION_PATH, text),
        backend="pi",
        flags=WRITER_FLAGS,
    )


def write_final_report(plan_summary, policy_summary, round_summaries, final_judge_result):
    report = "\n\n".join([
        ROUND_MARKER,
        "PLAN SUMMARY",
        plan_summary,
        ROUND_MARKER,
        "POLICY SUMMARY",
        policy_summary,
        ROUND_MARKER,
        "ROUND SUMMARIES",
        "\n\n---\n\n".join(round_summaries),
        ROUND_MARKER,
        "FINAL JUDGE RESULT",
        final_judge_result,
        ROUND_MARKER,
        "ARTIFACTS",
        "plan: " + PLAN_PATH,
        "policy: " + POLICY_PATH,
        "execution notes: " + EXECUTION_NOTES_PATH,
        "verification log: " + VERIFICATION_PATH,
    ])
    call_agent(
        """
Use Bash only.
Write the exact text below to %s, replacing file contents.

%s
""" % (FINAL_REPORT_PATH, report),
        backend="pi",
        flags=WRITER_FLAGS,
    )


def cleanup_round(round_id, exhausted):
    stream_summaries = run_implementation_round(round_id)
    integration_summary = integrate(round_id)
    verifier_results = verify_round(round_id)
    auditor_result = audit_round(round_id)
    judge_result = judge_round(round_id, integration_summary, verifier_results, auditor_result, exhausted)
    write_verification_log(round_id, verifier_results, integration_summary, auditor_result, judge_result)
    round_summary = "\n\n".join([
        "ROUND %s" % round_id,
        "STREAM SUMMARIES",
        "\n\n---\n\n".join(stream_summaries),
        "INTEGRATION",
        integration_summary,
        "AUDITOR",
        auditor_result,
        "JUDGE",
        judge_result,
    ])
    return round_summary, judge_result


def main():
    plan_summary = plan()
    policy_summary = write_policy()
    round_summaries = []
    final_judge_result = "VERDICT: RETRY_REQUIRED\n\nBLOCKERS:\n- Initial state not yet verified\n\nWARNINGS:\n- none\n\nEXTERNAL:\n- none"

    round_ids = [1, 2, 3, 4, 5, 6]
    for round_id in round_ids:
        exhausted = round_id == round_ids[-1]
        round_summary, final_judge_result = cleanup_round(round_id, exhausted)
        round_summaries.append(round_summary)
        if not verdict_has_blockers(final_judge_result):
            break
        if verdict_is_exhausted(final_judge_result):
            break

    write_final_report(plan_summary, policy_summary, round_summaries, final_judge_result)
    output(FINAL_REPORT_PATH)


main()
