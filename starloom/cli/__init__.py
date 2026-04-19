"""CLI — thin layer: parse args, typed config, delegate, render output."""

from __future__ import annotations

import click


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option(
    "--no-color",
    is_flag=True,
    default=False,
    envvar="NO_COLOR",
    is_eager=True,
    help="Disable ANSI color/styling in terminal output.",
)
@click.pass_context
def main(ctx: click.Context, no_color: bool) -> None:
    """Run and inspect Starloom workflows from the command line.

    Starloom executes Starlark workflow files as persisted sessions. Each
    session keeps its workflow source, config, event log, and trace graph on
    disk so you can attach live, replay later, inspect nodes, resume work, and
    answer checkpoints.

    \b
    Quick start:
      starloom session create workflow.star -p topic=AI
      starloom session attach SESSION_ID
      starloom node list
      starloom checkpoint answer CHECKPOINT_ID "continue"
      starloom session resume SESSION_ID

    \b
    Command groups:
      session      Create, attach, resume, stop, list, and delete sessions.
      node         Inspect nodes and request node-level changes.
      checkpoint   Resolve pending checkpoints in a running session.
      explain      Read concept-level help for sessions, nodes, and events.
      completions  Print shell completion setup for bash, zsh, or fish.
    """
    ctx.ensure_object(dict)
    ctx.obj["no_color"] = no_color
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit()


# ---------------------------------------------------------------------------
# Register subcommands
# ---------------------------------------------------------------------------

from starloom.cli.session import session  # noqa: E402
from starloom.cli.node import node  # noqa: E402
from starloom.cli.checkpoint import checkpoint_group  # noqa: E402
from starloom.cli.explain import explain  # noqa: E402

main.add_command(session)
main.add_command(node)
main.add_command(checkpoint_group, "checkpoint")
main.add_command(explain)


# ---------------------------------------------------------------------------
# completions
# ---------------------------------------------------------------------------


@main.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
def completions(shell: str) -> None:
    """Print shell completion setup for bash, zsh, or fish.

    The script is written to stdout so you can eval/source it directly or save
    it to a file and load it from your shell profile.
    """
    _print_completion(shell)


def _print_completion(shell: str) -> None:
    """Output the appropriate completion script."""
    var = "_STARLOOM_COMPLETE"
    scripts = {
        "bash": f'eval "$({var}=bash_source starloom)"',
        "zsh": f'eval "$({var}=zsh_source starloom)"',
        "fish": f"{var}=fish_source starloom | source",
    }
    instructions = (
        f"# Add this to your shell profile:\n"
        f"{scripts[shell]}\n"
        f"\n"
        f"# Or generate and source a static script:\n"
        f"# {var}={shell}_source starloom > ~/.starloom-complete.{shell}\n"
        f"# source ~/.starloom-complete.{shell}"
    )
    click.echo(instructions)
