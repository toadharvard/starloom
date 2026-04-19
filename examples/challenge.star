COST_REVIEW = """
Review: cost and economics

Review the starloom workflow at %s from a COST and ECONOMICS perspective.

Read the workflow file. Also read the starloom pattern catalog
(search for SKILL.md containing "Pattern catalog" in nearby dirs).

Also collect environment: read ~/.claude/settings.json and inspect any
relevant local Claude configuration that affects tool access or behavior.

Check:
- Model matches task complexity? Opus justified?
- Could parallel haiku replace one sonnet?
- Sequential where parallel is possible?
- Unnecessary agent calls that could be starlark logic?

POINTS OF NO RETURN:
- Plan/design verified before expensive execution?
- Explicit checkpoint before expensive execution?
- Error in plan = entire downstream work wasted?

For each issue: [CRITICAL/WARNING/INFO] description — fix.
No issues: CLEAN.
"""

ROBUSTNESS_REVIEW = """
Review: robustness and failures

Review the starloom workflow at %s from a ROBUSTNESS perspective.

Read the workflow file and the starloom pattern catalog.

Check:
- What if agent returns garbage or empty string?
- Loop never converges? Max iterations sufficient?
- parallel_map branch fails — workflow survives?
- Context lost between stages?
- Break condition checks right keyword? Keyword could appear accidentally?
- backend flags and tool scope correct? Reviewer agents should keep read-only tool access.
- def main() + main()? .format() or percent-s for substitutions?
- Mutable containers for cross-iteration state?
- Current session/replay terminology used consistently in prompts and outputs?

For each issue: [CRITICAL/WARNING/INFO] description — fix.
No issues: CLEAN.
"""

PROMPTS_REVIEW = """
Review: prompts and verification

Review the starloom workflow at %s from a PROMPTS and VERIFICATION perspective.

Read the workflow file and the starloom pattern catalog.
Also collect environment: read ~/.claude/settings.json and inspect any
relevant local Claude configuration that affects tool access or behavior.

PROMPTS:
- Each prompt specific and single-task?
- First line is a short agent name (max 40 chars), then blank line, then instructions?
- Response format specified (ALL_PASS, DONE, etc.)?
- Contradictions between prompts?
- Hardcoded content that should come from plan agent?
- Enough context for each agent?

VERIFICATION:
- Smoke test before main work?
- Metric can be gamed without solving real problem?
- Verifier same as implementer?

ENVIRONMENT:
- Backend selection and raw backend flags fit the workflow environment?
- MCP used where it fits?
- Tool scopes stay minimal for each agent role?
- Examples/help/checklists avoid removed first-class agent option language and use current backend+flags wording?

For each issue: [CRITICAL/WARNING/INFO] description — fix.
No issues: CLEAN.
"""

SYNTHESIS_PROMPT = """
Synthesize challenge report

Combine three independent reviews into a final challenge report.

REVIEW 1 (cost/economics):
%s

REVIEW 2 (robustness):
%s

REVIEW 3 (prompts/verification):
%s

Deduplicate. Order by severity. For each: [SEVERITY] category — description — fix.

Summary: N critical, N warning, N info.
Verdict: FAIL (has critical) / WARN (warnings only) / PASS.
Top 3 actions before running.
"""

PARAMS = [param("path", type="string", description="Path to .star file")]

def main():
    # path is injected by runtime from -p path=...

    # Crossed Blind Review — 3 full reviews in parallel, each reads the file itself
    reviews = parallel_map(
        lambda prompt: agent(prompt % path, flags="--model sonnet --tools read,grep,glob"),
        [COST_REVIEW, ROBUSTNESS_REVIEW, PROMPTS_REVIEW],
    )

    # Synthesis
    report = call_agent(
        SYNTHESIS_PROMPT % (reviews[0], reviews[1], reviews[2]),
        flags="--model sonnet",
    )

    output(report)

main()
