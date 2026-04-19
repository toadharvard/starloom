"""Design a clean multi-backend architecture migration for Starloom."""

REPO_ROOT = "~/ws/starloom2"
OUTPUT_PATH = "/tmp/starloom_multi_backend_architecture_report.txt"
MODEL = "codex-lb/gpt-5.4"
FLAGS = "--model %s --thinking xhigh --tools bash,read,grep" % MODEL

PLAN_PROMPT = """PLAN a clean architectural migration from single-backend assumptions to a true multi-backend execution model.
You are a principal architect working in %s.

Mission:
Design the implementation plan for making Starloom honestly multi-backend.
Do not implement code. Produce a concrete architecture + migration plan.

Current problem framing:
- WorkflowConfig.backend should mean default backend, not only backend.
- call_agent(..., backend=...) and agent(..., backend=...) should be real per-node overrides.
- Node execution should record the effective backend actually used.
- Stop routing should target the backend that owns the running node.
- Claude hook/checkpoint infrastructure is backend-specific and should remain so.
- Avoid overengineering and avoid fake generic capability systems unless truly needed.

Your output must help implementation, not just describe ideals.

Required output sections:
SUMMARY
CURRENT MODEL
TARGET MODEL
KEY DESIGN DECISIONS
PROPOSED EXECUTION ORDER
OPEN RISKS
""" % REPO_ROOT

MODEL_AUDIT_PROMPT = """AUDIT the current backend/orchestration model in %s.
Find the exact single-backend assumptions that must be removed or rewritten.

Focus on:
- runtime backend resolution
- orchestrator wiring
- worker construction
- stop routing
- persistence/graph metadata
- tests/docs/examples that encode one-backend assumptions

Required output sections:
TOP SINGLE-BACKEND ASSUMPTIONS
EVIDENCE
MUST-CHANGE SURFACES
CAN-STAY-AS-IS
""" % REPO_ROOT

REGISTRY_PROMPT = """DESIGN the backend registry / execution wiring for Starloom in %s.

Goal:
Replace the one-backend execution assumption with a clean architecture where:
- all available backends are known to the execution environment
- one backend is the default
- per-node override is resolved at execution time
- dry-run remains a separate execution mode, not a fake live backend model

Be concrete about module boundaries, types, ownership, and lifecycle.

Required output sections:
PROPOSED TYPES
RESPONSIBILITIES
WIRING CHANGES
DATA FLOW
MIGRATION STEPS
RISKS
""" % REPO_ROOT

GRAPH_PROMPT = """DESIGN the node/graph/persistence changes needed for honest multi-backend execution in %s.

Goal:
Specify what backend-related fields should be stored on node specs/results/graph records.
Focus on preserving correctness for UI, debugging, persistence, resume, and stop routing.

Explicitly answer:
- should requested backend be stored?
- should effective backend be stored?
- when is each value known?
- what must be persisted to graph.json?
- what migration strategy is needed for old sessions that lack these fields?

Required output sections:
DATA MODEL
PERSISTENCE CHANGES
RESUME / COMPATIBILITY
STOP-ROUTING IMPLICATIONS
RECOMMENDED CHOICE
""" % REPO_ROOT

HOOKS_PROMPT = """DESIGN the correct role of Claude-specific hooks/checkpoints in a multi-backend Starloom architecture in %s.

Important constraint:
Claude hook/checkpoint infrastructure is specific to Claude, not generic to all backends.
Do not invent fake generality.

Answer:
- what remains backend-specific?
- what should still move to generic orchestration?
- when should hook server start?
- how should Claude backend instances receive hook configuration?
- what should not be abstracted yet?

Required output sections:
CLAUDE-SPECIFIC CONCERNS
GENERIC ORCHESTRATION CONCERNS
RECOMMENDED WIRING
NON-GOALS / OVER-ABSTRACTIONS TO AVOID
""" % REPO_ROOT

STOP_PROMPT = """DESIGN correct node/session stop behavior for a multi-backend Starloom execution model in %s.

Goal:
Specify how stop_node and stop_session should work once nodes may run on different backends.

You must address:
- node-level stop routing
- session-level stop routing
- what metadata is required on graph nodes
- fallback behavior for legacy nodes missing backend metadata
- correctness vs simplicity tradeoffs

Required output sections:
DESIRED SEMANTICS
ROUTING MODEL
REQUIRED DATA
LEGACY FALLBACK
IMPLEMENTATION ORDER
RISKS
""" % REPO_ROOT

TEST_PROMPT = """DESIGN the test plan for migrating Starloom to honest multi-backend execution in %s.

Cover:
- unit tests
- integration tests
- persistence tests
- stop-routing tests
- mixed-backend workflow proofs
- Claude-hook compatibility proofs where applicable

Required output sections:
UNIT TESTS
INTEGRATION TESTS
REAL WORKFLOW PROOFS
TEST ORDER
MINIMUM ACCEPTANCE BAR
""" % REPO_ROOT

INTEGRATE_TEMPLATE = """INTEGRATE multiple architecture design reports into one implementation plan for Starloom multi-backend execution.
You are the lead architect in %s.

Inputs:
1. Planning report
%s

2. Current-model audit
%s

3. Registry/wiring design
%s

4. Graph/persistence design
%s

5. Claude-hooks design
%s

6. Stop-routing design
%s

7. Test-plan design
%s

Task:
Produce one coherent implementation plan that is pragmatic, architecturally clean, and avoids unnecessary abstraction.

Required output sections:
SUMMARY
WHAT IS ACTUALLY BROKEN TODAY
TARGET ARCHITECTURE
DESIGN PRINCIPLES
IMPLEMENTATION PHASES
- Phase 1
- Phase 2
- Phase 3
FILES / SUBSYSTEMS TO TOUCH
WHAT TO DELETE FROM THE OLD SINGLE-BACKEND MODEL
WHAT TO KEEP BACKEND-SPECIFIC
TEST / PROOF PLAN
RISKS / MIGRATION NOTES
FINAL RECOMMENDATION

Output only the report.
"""

WRITER_TEMPLATE = """Use Bash only.
Write the exact text below to %s, replacing file contents.

%s
"""


def plan():
    return call_agent(PLAN_PROMPT, backend="pi", flags=FLAGS)


def audit_current_model():
    prompts = [
        MODEL_AUDIT_PROMPT,
        REGISTRY_PROMPT,
        GRAPH_PROMPT,
        HOOKS_PROMPT,
        STOP_PROMPT,
        TEST_PROMPT,
    ]
    return parallel_map(lambda prompt: agent(prompt, backend="pi", flags=FLAGS), prompts)


def integrate(plan_report, reports):
    prompt = INTEGRATE_TEMPLATE % (
        REPO_ROOT,
        plan_report,
        reports[0],
        reports[1],
        reports[2],
        reports[3],
        reports[4],
        reports[5],
    )
    return call_agent(prompt, backend="pi", flags=FLAGS)


def write_report(report):
    prompt = WRITER_TEMPLATE % (OUTPUT_PATH, report)
    call_agent(prompt, backend="pi", flags="--model %s --thinking xhigh --tools bash" % MODEL)


def main():
    plan_report = plan()
    reports = audit_current_model()
    final_report = integrate(plan_report, reports)
    write_report(final_report)
    output(OUTPUT_PATH)


main()
