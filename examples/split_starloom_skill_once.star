"""One-off workflow that splits the monolithic starloom-workflow-author skill into child skills and generates a companion workflow."""

SOURCE_SKILL_PATH = "claude-plugin/skills/starloom-workflow-author/SKILL.md"
REFERENCE_STAR_PATH = "examples/review_starloom.star"
OUTPUT_ROOT = "tmp/starloom_skill_split"
CHILD_SKILLS_DIR = OUTPUT_ROOT + "/skills"
GENERATED_WORKFLOW_PATH = OUTPUT_ROOT + "/generated_split_workflow.star"
SUMMARY_PATH = OUTPUT_ROOT + "/summary.txt"

MODEL = "codex-lb/gpt-5.4"
ANALYSIS_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob" % MODEL
WRITE_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob,write" % MODEL
REVIEW_FLAGS = "--model %s --thinking xhigh --tools bash,read,grep,glob" % MODEL

ANALYZE_SOURCE_PROMPT = """Analyze source skill for one-off split generation
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.

Read these exact files:
- {source_skill_path}
- {reference_star_path}

Task:
Analyze the monolithic source skill and the reference `.star` example so later steps can split the skill into child skills and generate a companion workflow.

Focus on:
- the source skill's major methodological phases
- the source skill's explicit rules that materially affect workflow design
- the Starloom/Starlark constraints that must survive the split
- which parts look like shared doctrine vs specialized responsibilities
- which artifact shapes are implied by the reference `.star` example

Required output structure:
SUMMARY
- 5-10 sentences on what the source skill is trying to accomplish.

MAJOR PHASES
- bullet list of the main phases in order.

SHARED DOCTRINE CANDIDATES
- bullet list of rules/principles that should remain shared.

SPECIALIZABLE RESPONSIBILITIES
- bullet list of responsibilities that could become separate child skills.

ARTIFACT EXPECTATIONS
- bullet list describing what generated child skills and the generated workflow will need to look like.

Output only the analysis.
"""

EXTRACT_DOCTRINE_PROMPT = """Extract shared doctrine for split workflow
Primary skill:
- starloom-workflow-author

Work in ~/ws/starloom2.

Input analysis:
{source_analysis}

Task:
Extract the common doctrine that must remain invariant across all generated child skills.

The doctrine should preserve the original skill's logic about:
- Definition of Done before workflow design
- verification before flow shape
- bounded loops only when convergence is credible
- honest result statuses
- execution-tree review before code
- Starloom builtin discipline
- Starlark constraints that matter in Starloom

Hard requirement:
The decomposition target is exactly this three-skill split and nothing else:
- `starloom-dod-designer`
- `starloom-execution-architect`
- `starloom-workflow-author`

Do not rename these child skills.
Do not collapse them into fewer skills.
Do not invent alternative two-skill splits such as doctrine/writer or verification/reviewer.
The purpose of this doctrine artifact is to support that exact three-skill decomposition.

Required output structure:
DOCTRINE SUMMARY
- 6-12 bullets of the shared doctrine.

NON-NEGOTIABLE RULES
- bullet list of rules that all child skills must preserve.

SHARED LANGUAGE
- bullet list of preferred terms and distinctions that should remain stable.

Output only the doctrine artifact.
"""

DESIGN_SPLIT_PROMPT = """Design child-skill split for one-off artifact generation
Primary skill:
- starloom-execution-architect

Supporting skill:
- starloom-dod-designer

Work in ~/ws/starloom2.

Source analysis:
{source_analysis}

Doctrine artifact:
{doctrine}

Task:
Design a practical child-skill split for the monolithic source skill.

Requirements:
- preserve the shared doctrine without copying the entire monolith into every child skill
- keep the split compact rather than over-fragmented
- produce a set of child skills with distinct responsibilities
- include enough detail so a later writer can create the child `SKILL.md` files and the generated `.star`
- assume this is a one-off workflow, not a reusable framework product

Required output structure:
SPLIT SUMMARY
- 4-8 sentences describing the proposed split.

CHILD SKILLS
For each child skill use this exact structure:
## <skill-name>
ROLE: <what it does>
OWNS:
- <responsibility>
- <responsibility>
DOES NOT OWN:
- <boundary>
- <boundary>

SHARED DOCTRINE HANDLING
- explain how the shared doctrine should be referenced without re-monolithizing the child skills.

TARGET ARTIFACTS
- bullet list of the files that later steps should write.

Output only the split plan.
"""

WRITE_ARTIFACTS_PROMPT = """Write child skill files and generated workflow for approved split
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.
Use Bash and Write.

Write outputs under these exact paths:
- child skills root: {child_skills_dir}
- generated workflow path: {generated_workflow_path}
- summary path: {summary_path}

Use these inputs:

SOURCE ANALYSIS
{source_analysis}

DOCTRINE
{doctrine}

APPROVED SPLIT PLAN
{split_plan}

Task:
Write all generated artifacts for this one-off split task.

Requirements:
- create one directory per child skill under the child skills root
- each child skill must contain a `SKILL.md`
- write exactly these child skill directories and no others:
  - `starloom-dod-designer`
  - `starloom-execution-architect`
  - `starloom-workflow-author`
- do not create alternative child skills such as `starloom-workflow-doctrine`, `starloom-workflow-writer`, `starloom-verification-designer`, or `starloom-workflow-reviewer`
- write the child skills in a style informed by anthropics/skills `skill-creator`:
  - clear YAML frontmatter
  - pushy but accurate descriptions
  - compact, role-focused instructions
  - avoid duplicating the full monolithic methodology in every file
- preserve the approved split plan and shared doctrine
- write a generated `.star` workflow that orchestrates exactly the approved three child skills in order
- in generated workflow prompts, refer explicitly to the intended child skills by those exact names
- keep the generated workflow understandable and inspectable
- also write a compact summary file listing what was created

The summary file must contain:
- child skill directories created
- generated workflow path
- 1-2 sentence purpose summary per child skill

Return a concise artifact summary that includes all written paths.
"""

REVIEW_ARTIFACTS_PROMPT = """Review generated split artifacts against approved rubric
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.

Source analysis:
{source_analysis}

Doctrine artifact:
{doctrine}

Approved split plan:
{split_plan}

Generated artifact summary:
{artifact_summary}

Review rubric:
- doctrine preserved
- source methodology coverage is sufficient
- child skills have distinct responsibilities
- exactly three child skills exist: `starloom-dod-designer`, `starloom-execution-architect`, `starloom-workflow-author`
- no extra or alternative child skills exist
- generated workflow follows the approved split
- generated workflow prompts refer to the intended three skills by exact name
- no obvious re-monolithization
- no major Starloom/Starlark structure issues visible from the artifacts

Task:
Review the generated artifacts strictly against the rubric above.
Do not invent new requirements.
If there are issues, focus on blocking issues only.
If there are no blocking issues, say so clearly.

Required output structure:
STATUS: PASS | HUMAN_REVIEW | ACTIONABLE_FIXES | FAIL

SUMMARY
- short explanation of the current result.

BLOCKING ISSUES
- bullet list, or `- none`

SMALLEST NEXT ACTION
- one compact instruction for the next step, or `- none`

Output only the review.
"""

REMEDIATE_ARTIFACTS_PROMPT = """Remediate generated split artifacts after blocking review
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.
Use Bash and Write.

Use these inputs:

DOCTRINE
{doctrine}

APPROVED SPLIT PLAN
{split_plan}

CURRENT ARTIFACT SUMMARY
{artifact_summary}

REVIEW FINDINGS
{review_text}

Task:
Fix only the blocking issues identified by the reviewer.
Do not redesign the split.
Do not add new scope.
Keep all existing outputs in place and rewrite them as needed.
Update the summary file if paths or summaries need correction.

Return a concise summary of what was changed.
"""

FINAL_REVIEW_PROMPT = """Final review of remediated split artifacts
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.

Doctrine artifact:
{doctrine}

Approved split plan:
{split_plan}

Current artifact summary:
{artifact_summary}

Use the same rubric as the prior review:
- doctrine preserved
- source methodology coverage is sufficient
- child skills have distinct responsibilities
- exactly three child skills exist: `starloom-dod-designer`, `starloom-execution-architect`, `starloom-workflow-author`
- no extra or alternative child skills exist
- generated workflow follows the approved split
- generated workflow prompts refer to the intended three skills by exact name
- no obvious re-monolithization
- no major Starloom/Starlark structure issues visible from the artifacts

Task:
Perform the final review after the single remediation round.
Do not invent new requirements.
Be explicit and honest.

Required output structure:
STATUS: PASS | HUMAN_REVIEW | FAIL

SUMMARY
- short explanation.

REMAINING BLOCKERS
- bullet list, or `- none`

Output only the review.
"""


def analyze_source():
    prompt = ANALYZE_SOURCE_PROMPT.format(
        source_skill_path=SOURCE_SKILL_PATH,
        reference_star_path=REFERENCE_STAR_PATH,
    )
    return call_agent(prompt, backend="pi", flags=ANALYSIS_FLAGS)


def extract_doctrine(source_analysis):
    prompt = EXTRACT_DOCTRINE_PROMPT.format(source_analysis=source_analysis)
    return call_agent(prompt, backend="pi", flags=ANALYSIS_FLAGS)


def design_split(source_analysis, doctrine):
    prompt = DESIGN_SPLIT_PROMPT.format(
        source_analysis=source_analysis,
        doctrine=doctrine,
    )
    return call_agent(prompt, backend="pi", flags=ANALYSIS_FLAGS)


def write_artifacts(source_analysis, doctrine, split_plan):
    prompt = WRITE_ARTIFACTS_PROMPT.format(
        child_skills_dir=CHILD_SKILLS_DIR,
        generated_workflow_path=GENERATED_WORKFLOW_PATH,
        summary_path=SUMMARY_PATH,
        source_analysis=source_analysis,
        doctrine=doctrine,
        split_plan=split_plan,
    )
    return call_agent(prompt, backend="pi", flags=WRITE_FLAGS)


def review_artifacts(source_analysis, doctrine, split_plan, artifact_summary):
    prompt = REVIEW_ARTIFACTS_PROMPT.format(
        source_analysis=source_analysis,
        doctrine=doctrine,
        split_plan=split_plan,
        artifact_summary=artifact_summary,
    )
    return call_agent(prompt, backend="pi", flags=REVIEW_FLAGS)


def remediate_artifacts(doctrine, split_plan, artifact_summary, review_text):
    prompt = REMEDIATE_ARTIFACTS_PROMPT.format(
        doctrine=doctrine,
        split_plan=split_plan,
        artifact_summary=artifact_summary,
        review_text=review_text,
    )
    return call_agent(prompt, backend="pi", flags=WRITE_FLAGS)


def final_review(doctrine, split_plan, artifact_summary):
    prompt = FINAL_REVIEW_PROMPT.format(
        doctrine=doctrine,
        split_plan=split_plan,
        artifact_summary=artifact_summary,
    )
    return call_agent(prompt, backend="pi", flags=REVIEW_FLAGS)


def main():
    source_analysis = analyze_source()
    doctrine = extract_doctrine(source_analysis)
    split_plan = design_split(source_analysis, doctrine)

    split_plan_review = call_agent(
        """Review split plan for one-off skill split
Primary skill:
- starloom-workflow-author

Supporting skill:
- starloom-workflow-author

Work in ~/ws/starloom2.

Task:
Review the proposed split plan below only for obvious architectural mismatch with the current task.
Do not redesign it.
Do not add new scope.
If the split is reasonable for the stated one-off task, return exactly:
STATUS: APPROVED

If there is a blocker that would make writing artifacts premature, return exactly:
STATUS: REJECTED
REASON: <one short reason>

Split plan:
""" + split_plan,
        backend="pi",
        flags=REVIEW_FLAGS,
    )
    if "STATUS: REJECTED" in split_plan_review:
        fail("Split plan was rejected before artifact generation.\n\n" + split_plan_review)

    artifact_summary = write_artifacts(source_analysis, doctrine, split_plan)
    review_text = review_artifacts(source_analysis, doctrine, split_plan, artifact_summary)

    if "STATUS: PASS" in review_text:
        output("STATUS: verified success\n\nARTIFACT SUMMARY\n" + artifact_summary + "\n\nREVIEW\n" + review_text)
        return

    if "STATUS: HUMAN_REVIEW" in review_text:
        output("STATUS: human-approved result\n\nARTIFACT SUMMARY\n" + artifact_summary + "\n\nREVIEW\n" + review_text)
        return

    if "STATUS: ACTIONABLE_FIXES" in review_text:
        remediation_summary = remediate_artifacts(doctrine, split_plan, artifact_summary, review_text)
        final_review_text = final_review(doctrine, split_plan, remediation_summary)

        if "STATUS: PASS" in final_review_text:
            output("STATUS: verified success\n\nARTIFACT SUMMARY\n" + remediation_summary + "\n\nFINAL REVIEW\n" + final_review_text)
            return

        if "STATUS: HUMAN_REVIEW" in final_review_text:
            output("STATUS: human-approved result\n\nARTIFACT SUMMARY\n" + remediation_summary + "\n\nFINAL REVIEW\n" + final_review_text)
            return

        fail("Final review failed after remediation.\n\n" + final_review_text)

    fail("Artifact review failed.\n\n" + review_text)


main()
