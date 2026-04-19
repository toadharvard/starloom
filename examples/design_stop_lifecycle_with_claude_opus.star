"""Design-only stop-lifecycle workflow using Claude Code Opus.

Goal:
- Think deeply about the remaining stop-lifecycle cleanup.
- Produce a concrete design/remediation plan, not direct code edits.
- Keep strict ownership boundaries:
  - runtime owns node lifecycle/events
  - orchestrator owns final session status
  - server only routes intents / session-stop coordination
  - backend only interrupts and surfaces explicit stop signals

This workflow is intentionally design-focused and read-only.
"""

MODEL = "opus"
ROOT = "~/ws/starloom2"
REPORT_PATH = "/tmp/stop_lifecycle_claude_opus_report.txt"

FILES = """
- starloom/runtime.py
- starloom/orchestrator.py
- starloom/server.py
- starloom/backend/protocol.py
- starloom/backend/pi.py
- starloom/backend/claude_cli.py
- starloom/builtins/agents.py
- starloom/builtins/parallel.py
- starloom/builtins/context.py
- starloom/checkpoint.py
- starloom/checkpoint_events.py
- starloom/cli/session.py
- starloom/cli/node.py
- starloom/session/service.py
- tests/test_stop_lifecycle.py
- tests/test_orchestrator.py
- tests/test_runtime.py
- tests/test_builtins.py
- tests/test_server.py
- tests/test_cli_node.py
- tests/test_cli_session.py
- tests/test_session_service.py
- examples/fix_stop_lifecycle_architecture.star
"""

ARCH_CONTEXT = """
Desired invariants:
- runtime is single source of truth for node lifecycle transitions/events
- orchestrator is single source of truth for final persisted session status
- server does not invent lifecycle; it only routes stop intent and coordinates session-stop delivery
- backend only performs physical interruption and surfaces typed stop signal
- no string matching for stop semantics
- no synthetic RuntimeError(\"agent stopped\") or RuntimeError(\"parallel branch stopped\")
- stop_node and stop_session remain semantically distinct
- session stop should end in stopped, not completed/error, when it is a clean stop path
- semantic workflow failure should be explicit via fail(...), not inferred from report text

Known remaining issues to reason about:
- typed stop still degrades into builtin string error in some paths
- live round 3 reportedly ended with meta.status=error instead of stopped
- orchestrator still appears to mutate stop state in reconciliation fallback
- ownership boundary around session-stop signal may still be muddy
- tests still failing around typed stop without string matching
"""


def shared_preamble():
    return """
You are doing a design review only inside %s.
Read the relevant repository files completely before concluding.
Do not edit code.
Do not propose fake abstractions unless they materially simplify the existing design.
Prefer minimal changes that finish the stop-lifecycle cleanup.
Use the current repo reality, not historical assumptions.

Repository files to inspect:
%s

Architecture context:
%s
""" % (ROOT, FILES, ARCH_CONTEXT)


def prompt_inventory():
    return shared_preamble() + """
Task: produce a precise inventory of the remaining stop-lifecycle failures.

You must identify:
- exact places where typed stop degrades into ordinary error semantics
- exact places where runtime/orchestrator/server ownership is still blurred
- exact places where node-stop and session-stop semantics are conflated
- exact tests that appear to encode the unfinished behavior
- whether the current checkpoint/stop control plane is sufficient for a TUI/control surface later

Write /tmp/stop_lifecycle_claude_opus_inventory.txt with sections:
- REMAINING FAILURES
- OWNERSHIP LEAKS
- ERROR-PATH LEAKS
- TEST GAPS / TEST FAILURES
- MINIMAL FIX SURFACE
"""


def prompt_design():
    return shared_preamble() + """
Task: design the minimal architecture cleanup for remaining stop-lifecycle work.

Read:
- /tmp/stop_lifecycle_claude_opus_inventory.txt
- relevant repo files

Produce /tmp/stop_lifecycle_claude_opus_design.txt with:
- TARGET SEMANTICS
- LAYER OWNERSHIP TABLE
- TYPED STOP PROPAGATION PATH
- SESSION STOP VS NODE STOP
- WHAT MUST BE DELETED / SIMPLIFIED
- FILE-BY-FILE CHANGES
- TEST CHANGES
- RISKS / EDGE CASES

Requirements:
- no string matching fallback as final design
- no invented lifecycle events from server
- no orchestrator-owned node lifecycle except emergency reconciliation if absolutely unavoidable, and if unavoidable explain the exact boundary
- explain how builtin wrapping should stop converting clean stop into ordinary error
"""


def prompt_adversarial_review():
    return shared_preamble() + """
Task: act as a hostile reviewer of the proposed design.

Read:
- /tmp/stop_lifecycle_claude_opus_inventory.txt
- /tmp/stop_lifecycle_claude_opus_design.txt
- relevant repo files

Write /tmp/stop_lifecycle_claude_opus_review.txt with:
- VALID CRITICISMS
- OVERREACH / OVERENGINEERING
- MISSING EDGE CASES
- PLACES WHERE THE DESIGN STILL LEAKS OWNERSHIP
- WHAT A MINIMALER DESIGN WOULD LOOK LIKE
"""


def prompt_final_report():
    return shared_preamble() + """
Task: synthesize the final answer for the user.

Read:
- /tmp/stop_lifecycle_claude_opus_inventory.txt
- /tmp/stop_lifecycle_claude_opus_design.txt
- /tmp/stop_lifecycle_claude_opus_review.txt
- relevant repo files when needed

Write %s.

Required structure:
SUMMARY
- 5-10 lines maximum

ROOT CAUSES
- concrete bullet list

RECOMMENDED MINIMAL FIX PLAN
- ordered steps
- file-by-file

TEST / LIVE VERIFICATION PLAN
- targeted tests
- one real live stop scenario
- what outcome must be observed in meta.json / graph / workflow.end

NON-GOALS
- things not worth redesigning now

Important:
- this is a design report only
- do not claim convergence if you cannot justify it
- prefer surgical cleanup over architectural churn
""" % REPORT_PATH


def opus_flags():
    return "--model %s --verbose --dangerously-skip-permissions --permission-mode bypassPermissions" % MODEL


def main():
    inventory = call_agent(prompt_inventory(), backend="claude", flags=opus_flags())
    design = call_agent(prompt_design(), backend="claude", flags=opus_flags())
    review = call_agent(prompt_adversarial_review(), backend="claude", flags=opus_flags())
    final_report = call_agent(prompt_final_report(), backend="claude", flags=opus_flags())

    output("\n\n".join([
        "INVENTORY:\n" + inventory,
        "DESIGN:\n" + design,
        "REVIEW:\n" + review,
        "FINAL REPORT: " + REPORT_PATH,
        "FINAL REPORT SUMMARY:\n" + final_report,
    ]))


main()
