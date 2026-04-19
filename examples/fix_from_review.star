"""Plan and execute repo fixes from review findings using pi/Codex."""

FINDINGS_PATH = "/tmp/starloom_review_findings.txt"
ROUNDS_PATH = "/tmp/starloom_review_rounds.txt"
PLAN_PATH = "/tmp/starloom_fix_plan.txt"
EXECUTION_REPORT_PATH = "/tmp/starloom_fix_execution_report.txt"
FINAL_REPORT_PATH = "/tmp/starloom_fix_final_report.txt"

MODEL = "codex-lb/gpt-5.4"
PLANNER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
IMPLEMENTER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,edit,write" % MODEL
INTEGRATOR_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,edit,write" % MODEL
VERIFIER_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob" % MODEL

SHARED_REPO_CONTEXT = """You are working in ~/ws/starloom2.

Project goal:
- Keep the codebase clean, readable, layered, and semantically coherent.
- Respect clear separation of responsibility.
- Prefer simple explicit models over overloaded generic ones.
- Non-backend layers must not own backend semantics.
- Display/rendering concerns belong in UI/output layers, not in trace model types.
- Avoid compatibility shims unless explicitly requested.

Architectural principles you MUST use while reasoning:
- Clean Architecture (dependency direction, policy vs detail)
- SOLID, especially SRP / ISP / DIP / LSP
- Clean Code naming, cohesion, small interfaces, explicit intent
- DRY without creating false abstractions
- Prefer data models with clear invariants over ad hoc fields
- Prefer typed explicit unions/variants over semantic overloading

Repo layers:
CLI -> UI -> Session -> Orchestrator -> Runtime -> Foundation
Packages: types.py, checkpoint.py, events.py, event_data.py, server.py, client.py, hooks.py,
messages.py, orchestrator.py, runtime.py, backend/, builtins/, session/, graph_pkg/,
middleware/, ui/, cli/, _worker.py

Use Bash/Read/Grep/Glob/Edit/Write as needed.
Use tests to validate changes.
"""

PLANNER_PROMPT = SHARED_REPO_CONTEXT + """
You are the planner/architect.

Your job is NOT to immediately edit code. First understand the design findings and build a high-quality execution plan that follows clean code and architecture principles.

Inputs:
- Findings file: %s
- Review rounds file: %s (may or may not exist)

Tasks:
1. Read the findings file. Also read the rounds file if it exists.
2. Inspect the repository structure enough to understand the current architecture.
3. Deduplicate and normalize findings.
4. Separate real architectural problems from lower-priority cosmetic issues.
5. Produce an execution plan with 3 implementation streams that can be worked on mostly independently.
6. Each stream must contain:
   - goal
   - target files/packages
   - exact design intent
   - constraints / what must NOT be done
   - verification steps
7. Also define a final integration checklist.
8. Write the plan to %s.

Required output format in the plan:
- Executive summary
- Architectural principles to preserve
- Prioritized problem list
- STREAM 1
- STREAM 2
- STREAM 3
- Integration checklist
- Final acceptance criteria

Use Bash if needed to write the exact content to %s.
Return a concise confirmation and summary.
""" % (FINDINGS_PATH, ROUNDS_PATH, PLAN_PATH, PLAN_PATH)

IMPLEMENTER_TEMPLATE = SHARED_REPO_CONTEXT + """
You are implementation stream %s.

Primary inputs:
- Plan file: %s
- Findings file: %s
- Review rounds file: %s (if present)

Your responsibilities:
1. Read the plan and execute ONLY the work intended for STREAM %s.
2. Make real code changes in the repo.
3. Keep the architecture cleaner than before.
4. Avoid unrelated edits.
5. If a local improvement is necessary to preserve design coherence, keep it minimal and explain it.
6. Run targeted tests for the files you changed.
7. Append a short implementation note to %s in this format:
   [STREAM %s]
   - changed: ...
   - rationale: ...
   - tests: ...
   - remaining risks: ...

Do not write the final global report. Just do your stream well.
"""

INTEGRATOR_PROMPT = SHARED_REPO_CONTEXT + """
You are the integrator.

Inputs:
- Plan file: %s
- Execution notes file: %s
- Findings file: %s

Tasks:
1. Read the plan.
2. Inspect the current repo state after implementation streams.
3. Resolve inconsistencies across streams.
4. Ensure the final code follows the intended architecture:
   - clean boundaries
   - no presentation concerns in trace model
   - backend-specific semantics kept in backend-specific places
   - flags remain opaque outside backends
   - TraceNode remains a clean data holder with explicit payload variants
   - examples and help text use current terms like replayed event history, persisted workflow source, and trace graph
5. Run a broad but still practical test pass.
6. Write an integration/finalization report to %s.

The report must include:
- what was integrated
- what was corrected after stream work
- tests run
- any residual concerns
""" % (PLAN_PATH, EXECUTION_REPORT_PATH, FINDINGS_PATH, FINAL_REPORT_PATH)

COUPLING_REVIEW = SHARED_REPO_CONTEXT + """
Verification pass: Tight Coupling & Dependency Issues (Martin, Clean Architecture ch.14)

Read the current repo state and look for:
- Concrete class dependencies where an abstraction should be used
- Modules that import too many other modules
- Circular knowledge: A knows about B's internals, B knows about A
- Feature envy: function uses another class's data more than its own
- Inappropriate intimacy: accessing _private members across module boundaries
- God object: class with too many collaborators
- Dependency on implementation details rather than interfaces

Use the plan and previous findings only as context; judge the CURRENT code.
Return only remaining issues or explicitly say no major issue found.
"""

SRP_REVIEW = SHARED_REPO_CONTEXT + """
Verification pass: Single Responsibility Principle Violations (Martin, Clean Code ch.10)

Read the current repo state and look for:
- Classes or modules with multiple reasons to change
- Functions that do more than one thing (create AND validate AND persist)
- Modules that mix abstraction levels (high-level orchestration + low-level I/O)
- Data classes that also contain business logic
- Files that serve as dumping grounds for unrelated utilities
- Functions with boolean flag arguments that switch behavior

Return only remaining issues or explicitly say no major issue found.
"""

DRY_REVIEW = SHARED_REPO_CONTEXT + """
Verification pass: DRY Violations & Knowledge Duplication (Hunt/Thomas, Pragmatic Programmer)

Read the current repo state and look for:
- Same business logic expressed in multiple places
- Parallel structures that must change together
- Similar serialization/deserialization code repeated across modules
- Constants or magic values that represent the same concept but are defined separately
- Type definitions reconstructed from dicts in multiple places
- Event emission patterns copy-pasted across files

Return only remaining issues or explicitly say no major issue found.
"""

LSP_ISP_REVIEW = SHARED_REPO_CONTEXT + """
Verification pass: Interface Segregation & Liskov Substitution Issues (Martin, SOLID)

Read the current repo state and look for:
- Protocols/interfaces with methods that some implementers leave as no-op
- Base type contracts that subtypes don't fully honor
- Optional/unused parameters in function signatures
- Functions that accept a broad type but only use a narrow subset
- Empty data shapes that exist only to satisfy a union
- Protocol methods that no caller actually invokes

Return only remaining issues or explicitly say no major issue found.
"""

NAMING_COHESION_REVIEW = SHARED_REPO_CONTEXT + """
Verification pass: Naming Inconsistency & Low Cohesion (Martin, Clean Code ch.2; Fowler, Refactoring ch.3)

Read the current repo state and look for:
- Same concept named differently across modules
- Functions grouped in a module but not conceptually related
- Misleading names: function name promises X but does Y
- Inconsistent verb patterns: save/persist/write, load/read/get for same operation
- Type aliases that obscure rather than clarify
- Module names that don't match their actual responsibility

Return only remaining issues or explicitly say no major issue found.
"""

VERIFIER_PROMPTS = [
    COUPLING_REVIEW,
    SRP_REVIEW,
    DRY_REVIEW,
    LSP_ISP_REVIEW,
    NAMING_COHESION_REVIEW,
]


def plan():
    return call_agent(PLANNER_PROMPT, backend="pi", flags=PLANNER_FLAGS)


def implementation_stream(stream_id):
    prompt = IMPLEMENTER_TEMPLATE % (
        stream_id,
        PLAN_PATH,
        FINDINGS_PATH,
        ROUNDS_PATH,
        stream_id,
        EXECUTION_REPORT_PATH,
        stream_id,
    )
    return agent(prompt, backend="pi", flags=IMPLEMENTER_FLAGS)


def integrate():
    return call_agent(INTEGRATOR_PROMPT, backend="pi", flags=INTEGRATOR_FLAGS)


def verify_all():
    return parallel_map(
        lambda prompt: agent(prompt, backend="pi", flags=VERIFIER_FLAGS),
        VERIFIER_PROMPTS,
    )


def write_final_report(integration_summary, verifier_results):
    verifier_text = "\n\n---\n\n".join(verifier_results)
    prompt = """
Use Bash only.
Write the exact text below to %s, replacing file contents.

FINAL INTEGRATION SUMMARY
=========================
%s

VERIFICATION RESULTS
====================
%s
""" % (FINAL_REPORT_PATH, integration_summary, verifier_text)
    call_agent(prompt, backend="pi", flags="--model %s --thinking xhigh --tools bash" % MODEL)


def main():
    planning_summary = plan()
    streams = parallel_map(lambda i: implementation_stream(i), ["1", "2", "3"])
    integration_summary = integrate()
    verifier_results = verify_all()
    write_final_report(integration_summary, verifier_results)
    output(FINAL_REPORT_PATH)


main()
