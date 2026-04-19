"""Research pi plugin/package integration options for Starloom."""

PROMPT = """RESEARCH pi extension/package integration options for Starloom.
You are an implementation-focused pi extension architect working in the current repository.

Mission:
Figure out how far we can push pi to integrate with Starloom in a practical way.
The goal is not vague brainstorming. The goal is a realistic integration plan that could be installed and plausibly work.

You must study:
- local pi docs and example extensions/packages
- Starloom repo structure, sessions, events, nodes, checkpoints, replay/state files
- whether pi custom UI, overlays, footer/header/widgets, commands, tools, and package system can host a useful Starloom integration

Important framing:
- We want hope-backed-by-evidence that we can actually install something into pi and have it work.
- Focus on concrete extension/package mechanics, not hand-wavy product ideas.
- Distinguish what pi can do today vs what would require pi changes.

Questions to answer:
1. What exact integration forms are feasible today with pi extension APIs?
2. What should the integration read from Starloom? Session dirs, meta.json, graph.json, events.jsonl, workflow.star, checkpoints?
3. What custom commands/tools/UI should the extension expose?
4. Can it provide a useful live dashboard? A replay browser? Drill-down into nodes? Checkpoint actions? Session attach helpers?
5. What are the sharp edges, especially around TUI composition, background polling, state sync, and running external commands?
6. Should this be a single extension, a pi package, or multiple parts?
7. What is the minimal prototype we can build quickly that would still be genuinely useful?
8. What is the long-term architecture if the experiment works?

Required output structure:

SUMMARY
- concise verdict on feasibility

PI CAPABILITIES THAT MATTER
- enumerate the exact pi APIs, docs, and examples that are most relevant
- explain why each matters for a Starloom integration

STARLOOM DATA SURFACES
- list the Starloom files/commands/events/state surfaces the integration can rely on
- explain what each surface enables in the UI

FEASIBLE INTEGRATION SHAPES
For each realistic shape:
## <integration shape>
WHAT IT IS
WHY IT IS FEASIBLE
WHAT PI FEATURES IT USES
WHAT STARLOOM SURFACES IT USES
USER EXPERIENCE
LIMITATIONS

MINIMAL USEFUL PROTOTYPE
Design the smallest worthwhile pi extension/package for Starloom.
Include:
- commands
- widgets/overlays/footer/header
- polling or event ingestion model
- session selection model
- node/event drill-down model
- checkpoint interaction if feasible
- install shape
- file/package layout

BETTER FULL VERSION
Design the stronger long-term version.
Include module boundaries and likely files.

RISKS / UNKNOWNs
- what may fail or be awkward in practice
- what should be spike-tested first

RECOMMENDED BUILD PLAN
Atomic phases with concrete deliverables.

SOURCES APPENDIX
- exact docs/examples/files consulted

Rules:
- Use only evidence-backed claims when talking about pi capabilities.
- Cite local pi docs/examples by file path when relevant.
- Be explicit about what needs Starloom changes vs what can be done entirely in pi.
- Output only the report.
"""


def main():
    report = call_agent(PROMPT, backend="pi", flags="--model codex-lb/gpt-5.4 --thinking adaptive --tools bash,read")
    output(report)


main()
