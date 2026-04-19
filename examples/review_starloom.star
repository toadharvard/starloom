"""3 rounds of 5 parallel Codex reviewers find clean code violations in starloom2."""

SHARED_CONTEXT = """You review ~/ws/starloom2 — a Starlark workflow orchestrator (54 files, ~7200 LOC).

Layers (deps flow DOWN): CLI -> UI -> Session -> Orchestrator -> Runtime -> Foundation
Packages: types.py, checkpoint.py, events.py, event_data.py, server.py, client.py, hooks.py,
messages.py, orchestrator.py, runtime.py, backend/, builtins/, session/, graph_pkg/,
middleware/, ui/, cli/, _worker.py

Prefer the current architecture story while reviewing: session + persisted workflow source +
trace graph + replayed event history + backend-specific raw flags.

TOOLS: Use Bash to inspect the repo, and also Read/Grep/Glob.
Examples:
- find ~/ws/starloom2 -name '*.py' | sort
- rg -n 'class SessionServer|def execute\\(' ~/ws/starloom2

SHARED FINDINGS FILE: /tmp/starloom_review_findings.txt
- BEFORE you start, read this file if it exists.
- Other reviewers write there in parallel. Don't duplicate what is already present.
- Only append a finding if you did not already see the same idea in the file.
- Keep every appended line compact and specific.
- Append findings in this exact format:
    [CATEGORY] short description — file:symbol
- Use Bash for deduplicated append logic.
- At the end, also return your findings as text.
"""

COUPLING_REVIEW = SHARED_CONTEXT + """
FIND: Tight Coupling & Dependency Issues (Martin, Clean Architecture ch.14)

- Concrete class dependencies where an abstraction should be used
- Modules that import too many other modules
- Circular knowledge: A knows about B's internals, B knows about A
- Feature envy: function uses another class's data more than its own
- Inappropriate intimacy: accessing _private members across module boundaries
- God object: class with too many collaborators
- Dependency on implementation details rather than interfaces
"""

SRP_REVIEW = SHARED_CONTEXT + """
FIND: Single Responsibility Principle Violations (Martin, Clean Code ch.10)

- Classes or modules with multiple reasons to change
- Functions that do more than one thing (create AND validate AND persist)
- Modules that mix abstraction levels (high-level orchestration + low-level I/O)
- Data classes that also contain business logic
- Files that serve as dumping grounds for unrelated utilities
- Functions with boolean flag arguments that switch behavior
"""

DRY_REVIEW = SHARED_CONTEXT + """
FIND: DRY Violations & Knowledge Duplication (Hunt/Thomas, Pragmatic Programmer)

- Same business logic expressed in multiple places
- Parallel structures that must change together
- Similar serialization/deserialization code repeated across modules
- Constants or magic values that represent the same concept but are defined separately
- Type definitions reconstructed from dicts in multiple places
- Event emission patterns copy-pasted across files
"""

LSP_ISP_REVIEW = SHARED_CONTEXT + """
FIND: Interface Segregation & Liskov Substitution Issues (Martin, SOLID)

- Protocols/interfaces with methods that some implementers leave as no-op
- Base type contracts that subtypes don't fully honor
- Optional/unused parameters in function signatures
- Functions that accept a broad type but only use a narrow subset
- Empty data shapes that exist only to satisfy a union
- Protocol methods that no caller actually invokes
"""

NAMING_COHESION_REVIEW = SHARED_CONTEXT + """
FIND: Naming Inconsistency & Low Cohesion (Martin, Clean Code ch.2; Fowler, Refactoring ch.3)

- Same concept named differently across modules
- Functions grouped in a module but not conceptually related
- Misleading names: function name promises X but does Y
- Inconsistent verb patterns: save/persist/write, load/read/get for same operation
- Replay/reconstruct wording that drifts away from actual replayed event history behavior
- Type aliases that obscure rather than clarify
- Module names that don't match their actual responsibility
"""

FINDINGS_PATH = "/tmp/starloom_review_findings.txt"
REPORT_PATH = "/tmp/starloom_review_rounds.txt"
MODEL = "codex-lb/gpt-5.4"
REVIEW_PROMPTS = [
    COUPLING_REVIEW,
    SRP_REVIEW,
    DRY_REVIEW,
    LSP_ISP_REVIEW,
    NAMING_COHESION_REVIEW,
]

ROUND_MARKER = "================================================================"


def run_round(label):
    prompts = [label + "\n\n" + prompt for prompt in REVIEW_PROMPTS]
    return parallel_map(
        lambda prompt: agent(prompt, flags="--model %s --thinking xhigh --tools bash,read,grep,glob" % MODEL),
        prompts,
    )


def write_report(text):
    call_agent(
        """
Use Bash only.
Write the exact text below to %s, replacing file contents.

%s
""" % (REPORT_PATH, text),
        flags="--model %s --thinking xhigh --tools bash" % MODEL,
    )


def main():
    round1 = run_round("ROUND 1")
    round2 = run_round("ROUND 2")
    round3 = run_round("ROUND 3")

    report_text = "\n\n".join([
        ROUND_MARKER,
        "SHARED DEDUP FINDINGS FILE: " + FINDINGS_PATH,
        ROUND_MARKER,
        "ROUND 1",
        "\n\n---\n\n".join(round1),
        ROUND_MARKER,
        "ROUND 2",
        "\n\n---\n\n".join(round2),
        ROUND_MARKER,
        "ROUND 3",
        "\n\n---\n\n".join(round3),
    ])

    write_report(report_text)
    output(REPORT_PATH)


main()
