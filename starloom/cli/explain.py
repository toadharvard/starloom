"""CLI explain command — built-in help topics."""

from __future__ import annotations

import click

from starloom.cli.output import render_text


# ---------------------------------------------------------------------------
# Topics registry
# ---------------------------------------------------------------------------

_TOPICS: dict[str, str] = {
    "overview": (
        "Starloom is a Starlark workflow orchestrator for AI agents.\n"
        "\n"
        "Core concepts:\n"
        "  session    A single workflow execution with full trace graph\n"
        "  node       A trace node within a session (kind + payload + result)\n"
        "  checkpoint A pause point requiring operator decision\n"
        "  workflow   A .star file defining agent orchestration logic\n"
        "\n"
        "Use 'starloom explain <topic>' for details on any concept."
    ),
    "session": (
        "A session is a persisted execution of a workflow.\n"
        "\n"
        "Lifecycle: RUNNING -> COMPLETED | ERROR | STOPPED\n"
        "\n"
        "Sessions are stored in ~/.starloom/sessions/<id>/\n"
        "Each contains: meta.json, config.json, events.jsonl, graph.json, workflow.star\n"
        "workflow_file in meta/config is the original path; workflow.star is the persisted source copy."
    ),
    "node": (
        "A node represents one execution step in the trace graph.\n"
        "\n"
        "Each node has common lifecycle fields plus a kind-specific payload.\n"
        "Agent nodes carry an AgentSpec payload. Checkpoint nodes carry a question payload.\n"
        "Nodes can be patched and re-executed when that kind supports it."
    ),
    "node.status": (
        "Node lifecycle states:\n"
        "  PENDING    Waiting to execute\n"
        "  RUNNING    Currently executing\n"
        "  COMPLETED  Finished successfully\n"
        "  ERROR      Failed with error\n"
        "  SKIPPED    Skipped by workflow logic\n"
        "  CACHED     Result loaded from cache\n"
        "  STOPPED    Manually stopped"
    ),
    "node.cost": (
        "Telemetry per node:\n"
        "  cost_usd         Real backend-reported USD cost, if available\n"
        "  input_tokens     Input tokens consumed, if reported\n"
        "  output_tokens    Output tokens generated, if reported\n"
        "\n"
        "Starloom does not invent live-run costs. Unknown values are shown as '-'."
    ),
    "checkpoint": (
        "Checkpoints pause execution for operator input or decisions.\n"
        "\n"
        "Two current flows:\n"
        "  tool-call checkpoint   Backend-driven tool interception (approve/reject)\n"
        "  checkpoint()           Workflow-authored pause for an operator response\n"
        "\n"
        "Tool-call checkpoints depend on backend support. Explicit checkpoint() always pauses."
    ),
    "event": (
        "Events are emitted throughout workflow execution.\n"
        "\n"
        "Each event has: type, timestamp, session_id, node_id, data.\n"
        "Events are stored in events.jsonl (one JSON object per line).\n"
        "Inspect them with: starloom session attach <session-id> -o events\n"
        "Or read ~/.starloom/sessions/<id>/events.jsonl directly."
    ),
    "event.types": (
        "Events emitted during workflow execution:\n"
        "\n"
        "Workflow:    workflow.start, workflow.end\n"
        "Node:        node.added, node.started, node.finished,\n"
        "             node.error, node.skipped, node.cached, node.stopped\n"
        "Agent:       tool.call.start, tool.call.end, agent.text\n"
        "Checkpoint:  checkpoint.pending, checkpoint.resolved\n"
        "Cost:        cost.update"
    ),
    "workflow": (
        "Workflows are Starlark (.star) files defining agent orchestration.\n"
        "\n"
        "Key builtins:\n"
        "  call_agent(prompt, backend=, flags=)  Run an agent immediately, return output\n"
        "  agent(prompt, backend=, flags=)       Build a parallel_map work item\n"
        "  parallel_map(fn, items)               Run fn(item) concurrently\n"
        "  checkpoint(question)                  Pause for an operator response\n"
        "  output(value)                         Emit an output block (print-like)\n"
        "  param(name, type=, default=)          Declare a parameter"
    ),
    "workflow.params": (
        "Declare typed parameters with PARAMS = [param(...)]:\n"
        "\n"
        "  PARAMS = [\n"
        '      param("topic", type="string", default="AI"),\n'
        '      param("count", type="int", default=3),\n'
        "  ]\n"
        "\n"
        "Supported types: string, int, float, bool, list\n"
        "Pass values: starloom session create workflow.star -p topic=Space"
    ),
    "workflow.builtins": (
        "call_agent(prompt, backend=, flags=)\n"
        "  Run an agent immediately and return its text output.\n\n"
        "agent(prompt, backend=, flags=)\n"
        "  Build an agent spec for parallel_map; it does not execute by itself.\n\n"
        "parallel_map(fn, items)\n"
        "  Run fn(item) concurrently for each item. Return results in input order.\n\n"
        "checkpoint(question)\n"
        "  Pause workflow and wait for an operator response.\n\n"
        "output(value)\n"
        "  Emit an output block (print-like). Each call emits a workflow.output event;\n"
        "  there is no programmatic final-result slot.\n\n"
        "param(name, type=, default=, description=)\n"
        "  Declare a workflow parameter."
    ),
    "models": (
        "Model selection is backend-specific.\n"
        "\n"
        "Pass raw backend flags, for example:\n"
        "  flags='--model haiku'\n"
        "  flags='--model codex-lb/gpt-5.4-mini'"
    ),
    "cost": (
        "Usage telemetry is tracked for every session.\n"
        "\n"
        "Per-node: input_tokens, output_tokens, cost_usd\n"
        "Session-level: total_cost_usd\n"
        "\n"
        "Live runs store only backend-reported values. Dry-run uses tokenizer-based estimates."
    ),
}


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------


@click.command()
@click.argument("topic", required=False, default=None)
def explain(topic: str | None) -> None:
    """Show concept help for Starloom terms and mechanics.

    Run without TOPIC to list everything available.

    \b
    Examples:
      starloom explain overview
      starloom explain session
      starloom explain workflow.builtins
    """
    if topic is None:
        _list_topics()
        return
    _show_topic(topic)


def _list_topics() -> None:
    """Print available topics."""
    lines = ["Available topics:\n"]
    for name in sorted(_TOPICS):
        first_line = _TOPICS[name].split("\n", 1)[0]
        lines.append(f"  {name:<20} {first_line}")
    lines.append("\nUse: starloom explain <topic>")
    render_text("\n".join(lines))


def _show_topic(topic: str) -> None:
    """Print a single topic's explanation."""
    key = topic.lower().strip()
    if key not in _TOPICS:
        available = ", ".join(sorted(_TOPICS))
        raise click.ClickException(f"Unknown topic: {topic}\nAvailable: {available}")
    render_text(_TOPICS[key])
