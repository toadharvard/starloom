"""Implement honest multi-backend execution architecture for Starloom."""

REPO_ROOT = "."
MODEL = "codex-lb/gpt-5.4"
FLAGS = "--model %s --thinking xhigh --tools bash,read,grep" % MODEL
IMPLEMENTER_FLAGS = "--model %s --thinking xhigh --tools bash,read,edit,write,grep" % MODEL
WRITER_FLAGS = "--model %s --thinking xhigh --tools bash" % MODEL
PLAN_PATH = "/tmp/starloom_multi_backend_impl_plan.txt"
EXECUTION_PATH = "/tmp/starloom_multi_backend_impl_execution.txt"
VERIFICATION_PATH = "/tmp/starloom_multi_backend_impl_verification.txt"
FINAL_REPORT_PATH = "/tmp/starloom_multi_backend_impl_final_report.txt"

PLAN_PROMPT = """PLAN the implementation of a clean multi-backend architecture for Starloom in %s.

Goal:
Implement the architecture, not just describe it.

Requirements:
- WorkflowConfig.backend means default backend only.
- call_agent(..., backend=...) and agent(..., backend=...) must work as real per-node overrides.
- Remove single-backend execution assumptions from orchestration/runtime wiring.
- Record the effective backend used by each executed node.
- Stop routing should use node backend ownership, not a global single backend assumption.
- Claude hook/checkpoint infrastructure may remain Claude-specific.
- Avoid unnecessary abstractions and over-generalized capability systems.

Produce a concrete implementation plan with streams that can be done in parallel while keeping the repo working.
Also identify the most dangerous migration points.

Required output sections:
SUMMARY
IMPLEMENTATION STREAMS
ORDERING CONSTRAINTS
RISKS
ACCEPTANCE BAR

Also use Bash to write your exact plan to %s.
Return a short summary too.
""" % (REPO_ROOT, PLAN_PATH)

IMPLEMENTER_TEMPLATE = """IMPLEMENT stream %s of the multi-backend architecture migration in %s.

You are working in this repository and must make real code changes.
Follow the plan in %s and keep the codebase working.

Current execution log file: %s
Append a short section describing exactly what you changed, what tests you ran, and any follow-up notes.
Use Bash for appending to the execution log.

Streams:
1. runtime + orchestrator + worker backend registry/default-backend semantics
2. graph/persistence/effective-backend recording and any related compatibility handling
3. stop routing + tests + workflow proof scaffolding

You are stream %s.
Only do the work for your stream, but read neighboring code as needed.
Keep changes coherent and minimal.
Run focused tests relevant to your stream.
Output a concise summary of what you changed and verification done.
"""

INTEGRATOR_PROMPT = """INTEGRATE the multi-backend architecture implementation in %s.

Inputs:
- Plan file: %s
- Execution log: %s

Task:
Review the repo state after implementation streams finished.
Make any necessary integration fixes so the architecture is coherent end-to-end.
Then write a concise integration summary to %s using Bash.

Focus on:
- no remaining central single-backend assumption in runtime/orchestration path
- default backend semantics are clear
- per-node override works by architecture, not accident
- effective backend is recorded where needed
- stop routing is coherent
- Claude-specific hook behavior remains intact

Run focused tests after integration.
Return only the integration summary.
""" % (REPO_ROOT, PLAN_PATH, EXECUTION_PATH, EXECUTION_PATH)

VERIFIER_PROMPTS = [
    """VERIFY backend-resolution semantics in %s.
Check that default backend and per-node override semantics are implemented coherently in runtime/orchestrator/worker paths.
Run focused tests and inspect code.
Write findings to %s using Bash append.
Required sections: STATUS, EVIDENCE, FAILURES, RISKS.
""" % (REPO_ROOT, VERIFICATION_PATH),
    """VERIFY graph/persistence/metadata semantics in %s.
Check whether effective backend recording is implemented correctly and persisted where needed.
Run focused tests and inspect code.
Write findings to %s using Bash append.
Required sections: STATUS, EVIDENCE, FAILURES, RISKS.
""" % (REPO_ROOT, VERIFICATION_PATH),
    """VERIFY stop routing and backend-specific hook behavior in %s.
Check node/session stop behavior in mixed-backend execution and ensure Claude-specific hooks/checkpoints still make sense.
Run focused tests and inspect code.
Write findings to %s using Bash append.
Required sections: STATUS, EVIDENCE, FAILURES, RISKS.
""" % (REPO_ROOT, VERIFICATION_PATH),
]

FINAL_TEMPLATE = """Use Bash only.
Write the exact text below to %s, replacing file contents.

PLAN
====
%s

EXECUTION
=========
%s

VERIFICATION
============
%s

FINAL SUMMARY
=============
%s
"""


def plan():
    return call_agent(PLAN_PROMPT, backend="pi", flags=FLAGS)


def implementation_stream(stream_id):
    prompt = IMPLEMENTER_TEMPLATE % (
        stream_id,
        REPO_ROOT,
        PLAN_PATH,
        EXECUTION_PATH,
        stream_id,
    )
    return agent(prompt, backend="pi", flags=IMPLEMENTER_FLAGS)


def integrate():
    return call_agent(INTEGRATOR_PROMPT, backend="pi", flags=IMPLEMENTER_FLAGS)


def verify_all():
    return parallel_map(lambda prompt: agent(prompt, backend="pi", flags=FLAGS), VERIFIER_PROMPTS)


def write_final_report(plan_summary, integration_summary, verifier_results):
    verifier_text = "\n\n---\n\n".join(verifier_results)
    prompt = FINAL_TEMPLATE % (
        FINAL_REPORT_PATH,
        plan_summary,
        integration_summary,
        verifier_text,
        "Implementation completed. See plan/execution/verification sections above.",
    )
    call_agent(prompt, backend="pi", flags=WRITER_FLAGS)


def main():
    planning_summary = plan()
    approved = checkpoint("Review the proposed multi-backend implementation plan in attach. Reply OK to start real code changes, or REJECT with guidance if the plan is not acceptable yet.")
    if approved != "OK":
        output("Implementation cancelled at planning checkpoint: " + approved)
        return
    streams = parallel_map(lambda i: implementation_stream(i), ["1", "2", "3"])
    integration_summary = integrate()
    verifier_results = verify_all()
    write_final_report(planning_summary, integration_summary, verifier_results)
    output(FINAL_REPORT_PATH)


main()
