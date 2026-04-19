# Runnable autonomous multi-agent flow for stop-lifecycle redesign.
# No top-level loops. Bounded remediation rounds are unrolled explicitly.

MODEL = "codex-lb/gpt-5.4"
ROOT = "."
FINAL_REPORT = "/tmp/stopflow_final_report.txt"


def report_has_pass(text):
    return "VERDICT: PASS" in text or "final verdict: PASS" in text


def require_pass_or_fail(verdict1, verdict2, verdict3):
    if report_has_pass(verdict1):
        return
    if report_has_pass(verdict2):
        return
    if report_has_pass(verdict3):
        return
    fail("stop-lifecycle architecture workflow did not converge to PASS")

RUNTIME_FILES = """
- starloom/server.py
- starloom/orchestrator.py
- starloom/runtime.py
- starloom/builtins/agents.py
- starloom/builtins/parallel.py
- starloom/builtins/context.py
- starloom/backend/protocol.py
- starloom/backend/pi.py
- starloom/backend/claude_cli.py
"""

CONTROL_FILES = """
- starloom/session/service.py
- starloom/cli/node.py
- starloom/cli/session.py
- starloom/client.py
- starloom/messages.py
"""

TEST_FILES = """
- tests/test_stop_lifecycle.py
- tests/test_orchestrator.py
- tests/test_server.py
- tests/test_runtime.py
- tests/test_cli_node.py
- tests/test_cli_session.py
- tests/test_session_service.py
"""

GOALS = """
Global goals:
- runtime owns workflow.start/end and node lifecycle transitions/events
- orchestrator owns final session terminal status persistence
- server only routes stop intent and sets session-stop signal for stop_session
- backend only performs physical interruption and surfaces StopRequestedError
- no string matching for stop semantics
- no server-emitted fake lifecycle events
- stop_node and stop_session are semantically distinct
- stop_session => SessionStatus.STOPPED strictly
- workflow.end.error == null for stopped session flow
- CLI stop commands report failures truthfully
- no weakened tests and no STOPPED-or-COMPLETED ambiguity
- minimal design, no unnecessary new abstractions
"""


def pi_flags(extra_tools):
    return "--model %s --thinking high --tools %s" % (MODEL, extra_tools)


def inventory_runtime_prompt():
    return """
Think carefully and produce a runtime/backend/orchestrator inventory.
Repository: %s
Read these files completely:
%s
Write /tmp/stopflow_inventory_runtime.txt with:
- current stop flow by layer
- violations of ownership
- places where stop_node and stop_session are conflated
- simplification opportunities
- file/function-level recommendations
Do not edit code.
""" % (ROOT, RUNTIME_FILES)


def inventory_control_prompt():
    return """
Think carefully and produce a control-plane/CLI inventory.
Repository: %s
Read these files completely:
%s
Write /tmp/stopflow_inventory_control.txt with:
- stop request delivery paths
- truthful vs lying acknowledgements
- all failure modes that must be surfaced
- file/function-level recommendations
Do not edit code.
""" % (ROOT, CONTROL_FILES)


def inventory_tests_prompt():
    return """
Think carefully and produce a strict test inventory.
Repository: %s
Read these files completely:
%s
Write /tmp/stopflow_inventory_tests.txt with:
- current invariants covered
- missing invariants
- any ambiguous or weakened assertions
- exact strict tests that must exist after implementation
Do not edit code.
""" % (ROOT, TEST_FILES)


def design_prompt():
    return """
Think carefully. Using these inventory artifacts:
- /tmp/stopflow_inventory_runtime.txt
- /tmp/stopflow_inventory_control.txt
- /tmp/stopflow_inventory_tests.txt
Create the primary implementation design in /tmp/stopflow_design.txt.
It must be file-oriented and minimal.
Include:
- single source of truth rules
- layer responsibilities
- exact semantics for stop_node and stop_session
- exact ownership of workflow.start/end
- exact ownership of node transitions/events
- exact ownership of session finalization
- file-by-file implementation steps
- file-by-file test changes
- explicit rejection of extra abstractions not needed for this repo
Do not edit code.
"""


def design_review_a_prompt():
    return """
Act as a hostile architecture critic.
Read:
- /tmp/stopflow_design.txt
- relevant repository files as needed
Write /tmp/stopflow_design_review_a.txt with:
- all design flaws
- layering leaks
- hidden ambiguity
- any overengineering
- exact required corrections
Do not edit code.
"""


def design_review_b_prompt():
    return """
Act as an adversarial test critic.
Read:
- /tmp/stopflow_design.txt
- /tmp/stopflow_inventory_tests.txt
- relevant repository files as needed
Write /tmp/stopflow_design_review_b.txt with:
- missing invariants
- opportunities for false-green tests
- any insufficient live verification
- exact required corrections to the design
Do not edit code.
"""


def design_reconcile_prompt():
    return """
Reconcile the design with both hostile reviews.
Read:
- /tmp/stopflow_design.txt
- /tmp/stopflow_design_review_a.txt
- /tmp/stopflow_design_review_b.txt
Write the final approved design to /tmp/stopflow_design_reconciled.txt.
It must incorporate all valid criticisms and remain minimal.
Do not edit code.
"""


def implement_prompt(round, prev_round):
    prev_clause = ""
    if prev_round != "":
        prev_clause = "- /tmp/stopflow_adjudication_round_%s.txt\n" % prev_round
    return """
Think carefully and implement round %s using the reconciled design.
Read:
- /tmp/stopflow_design_reconciled.txt
%sRequirements:
- if this is round 1, implement the full design
- if this is a later round, implement only the required remediations from the prior adjudication
- keep the code minimal and layered correctly
- do not weaken tests
- remove ugly logic when replacing it
After edits, write /tmp/stopflow_impl_report_round_%s.txt with:
- files changed
- code deleted/simplified
- invariants targeted this round
- any unresolved concerns
""" % (round, prev_clause, round)


def verify_strict_prompt(round):
    return """
Run strict stop-lifecycle verification for round %s.
Command:
uv run pytest tests/test_stop_lifecycle.py tests/test_orchestrator.py tests/test_server.py tests/test_runtime.py tests/test_cli_node.py tests/test_cli_session.py tests/test_session_service.py -q
Write /tmp/stopflow_verify_strict_round_%s.txt with:
- exact command
- pass/fail summary
- invariant-by-invariant assessment
- raw failures if any
""" % (round, round)


def verify_full_prompt(round):
    return """
Run full repository verification for round %s.
Commands:
- uv run pytest -q
- uv run ruff check starloom tests
- uv run mypy starloom tests
Write /tmp/stopflow_verify_full_round_%s.txt with:
- command outputs summary
- any failures
- whether the tree is globally healthy
""" % (round, round)


def verify_live_prompt(round):
    return """
Run live PI verification for round %s.
Create a temporary workflow that:
- uses backend=pi
- invokes bash tool with sleep 30
Start the session, wait until tool.call.start appears, stop the node via CLI, inspect session files, and verify:
- meta.json status == stopped
- meta.json error == null
- graph node status == stopped
- workflow.end.error == null
Write /tmp/stopflow_verify_live_round_%s.txt with:
- exact commands
- exact observed evidence
- pass/fail conclusion
""" % (round, round)


def verify_arch_prompt(round):
    return """
Run an architecture boundary review for round %s.
Read:
- /tmp/stopflow_design_reconciled.txt
- current repository code
- /tmp/stopflow_impl_report_round_%s.txt
Verify:
- runtime owns workflow.start/end and node lifecycle transitions/events
- orchestrator owns final session status
- server only routes stop and sets session-stop signal for stop_session
- backend only interrupts and surfaces StopRequestedError
- no string matching for stop semantics
- no weakened tests remain
Write /tmp/stopflow_verify_arch_round_%s.txt with PASS/FAIL and concrete evidence.
""" % (round, round, round)


def adjudicate_prompt(round):
    return """
Act as final adjudicator for round %s.
Read:
- /tmp/stopflow_impl_report_round_%s.txt
- /tmp/stopflow_verify_strict_round_%s.txt
- /tmp/stopflow_verify_full_round_%s.txt
- /tmp/stopflow_verify_live_round_%s.txt
- /tmp/stopflow_verify_arch_round_%s.txt
Write /tmp/stopflow_adjudication_round_%s.txt.
Required format:
- VERDICT: PASS or FAIL
- GOAL STATUS: each global goal with satisfied/not satisfied
- BLOCKERS: explicit list
- REMEDIATION: file-by-file concrete actions if FAIL
Rules:
- PASS only if every verification artifact passes and architecture ownership is correct
- FAIL if any test is weakened, any invariant is ambiguous, or any artifact disagrees
- do not edit code
Goals reference:
%s
""" % (round, round, round, round, round, round, round, GOALS)


def final_report_prompt():
    return """
Create %s.
Read:
- /tmp/stopflow_design_reconciled.txt
- /tmp/stopflow_adjudication_round_1.txt if it exists
- /tmp/stopflow_adjudication_round_2.txt if it exists
- /tmp/stopflow_adjudication_round_3.txt if it exists
- latest implementation and verification artifacts that exist
The report must contain:
- final verdict (PASS if any round passed, else FAIL)
- which round converged, if any
- files changed
- final architecture summary
- final invariant matrix
- evidence from strict tests, full checks, and live PI verification
- if FAIL after round 3, all remaining blockers verbatim
""" % FINAL_REPORT


def run_round(round, prev_round):
    call_agent(implement_prompt(round, prev_round), backend="pi", flags=pi_flags("read,edit,write,bash"))
    parallel_map(
        lambda prompt: agent(prompt, flags=pi_flags("bash,read,write")),
        [
            verify_strict_prompt(round),
            verify_full_prompt(round),
            verify_live_prompt(round),
            verify_arch_prompt(round),
        ],
    )
    return call_agent(adjudicate_prompt(round), backend="pi", flags=pi_flags("read,write,bash"))


def main():
    parallel_map(
        lambda prompt: agent(prompt, flags=pi_flags("read,write,bash")),
        [
            inventory_runtime_prompt(),
            inventory_control_prompt(),
            inventory_tests_prompt(),
        ],
    )

    call_agent(design_prompt(), backend="pi", flags=pi_flags("read,write,bash"))
    parallel_map(
        lambda prompt: agent(prompt, flags=pi_flags("read,write,bash")),
        [
            design_review_a_prompt(),
            design_review_b_prompt(),
        ],
    )
    call_agent(design_reconcile_prompt(), backend="pi", flags=pi_flags("read,write,bash"))

    verdict1 = run_round("1", "")
    verdict2 = run_round("2", "1")
    verdict3 = run_round("3", "2")

    call_agent(final_report_prompt(), backend="pi", flags=pi_flags("read,write,bash"))
    require_pass_or_fail(verdict1, verdict2, verdict3)
    output("\n\n".join([
        "ROUND 1 ADJUDICATION:\n" + verdict1,
        "ROUND 2 ADJUDICATION:\n" + verdict2,
        "ROUND 3 ADJUDICATION:\n" + verdict3,
        "FINAL REPORT: " + FINAL_REPORT,
    ]))


main()
