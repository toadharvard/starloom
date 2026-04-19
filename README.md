# Starloom

Starloom runs deterministic [Starlark](https://github.com/bazelbuild/starlark) workflows that orchestrate groups of AI coding agents. You describe *what should happen* — which agents run, in what order, what each one sees, how results combine — as a `.star` file; Starloom executes it as a persisted, inspectable, resumable session.

```python
# examples/simple_agent.star
def main():
    result = call_agent("Say hello in one word", flags="--model haiku")
    output(result)

main()
```

```bash
starloom session create examples/simple_agent.star
```

Every run is a session on disk: graph, events, costs, stdout — all replayable. You can attach to a live run, resume a stopped one, patch a node's prompt or flags and re-run, or gate execution at workflow-authored checkpoints.

---

## Why Starlark?

Starlark gives you a real, hermetic scripting language — conditionals, loops, functions, parallel fan-out — without the non-determinism of letting an LLM decide control flow. The orchestration is deterministic code. The work inside each agent call is the only place the model gets to improvise.

---

## Install the CLI

Requires Python 3.11+ and the [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (`claude`) on `$PATH`.

**With [`uv`](https://docs.astral.sh/uv/) (recommended)** — installs `starloom` as a standalone tool on `$PATH`:

```bash
uv tool install git+https://github.com/toadharvard/starloom.git
```

**With `pip`**:

```bash
pip install git+https://github.com/toadharvard/starloom.git
```

**From a clone** (if you want the `examples/` directory locally):

```bash
git clone https://github.com/toadharvard/starloom.git
cd starloom
uv pip install -e .        # or: pip install -e .
```

Verify:

```bash
starloom --help
```

---

## Shell completions

Pick your shell and add the one-liner to your shell's rc file:

```bash
# bash — append to ~/.bashrc
eval "$(_STARLOOM_COMPLETE=bash_source starloom)"
```

```zsh
# zsh — append to ~/.zshrc
eval "$(_STARLOOM_COMPLETE=zsh_source starloom)"
```

```fish
# fish — append to ~/.config/fish/config.fish
_STARLOOM_COMPLETE=fish_source starloom | source
```

Run `starloom completions {bash|zsh|fish}` to re-print the snippet for your shell.

For faster shell startup, generate the script once and source it:

```bash
_STARLOOM_COMPLETE=zsh_source starloom > ~/.starloom-complete.zsh
# then in ~/.zshrc:
source ~/.starloom-complete.zsh
```

---

## Install the Claude Code plugin

The repo ships a Claude Code plugin (`claude-plugin/`) with two skills:

- **starloom-cli-operator** — drives `starloom` from inside Claude Code: creates sessions, attaches, patches nodes, resolves checkpoints.
- **starloom-workflow-implementer** — writes clean `.star` files from an already-approved architecture.

Clone the repo and symlink the plugin into Claude Code's plugins directory:

```bash
git clone https://github.com/toadharvard/starloom.git
mkdir -p ~/.claude/plugins
ln -s "$(pwd)/starloom/claude-plugin" ~/.claude/plugins/starloom
```

Restart Claude Code. The skills become available automatically when the model detects a matching task.

---

## Core primitives

Available inside a `.star` file:

| Primitive | What it does |
| --- | --- |
| `call_agent(prompt, *, flags="")` | Run one agent, block, return its final text. |
| `agent(prompt, *, flags="")` | Return an agent *spec* (not yet run). Pass specs to `parallel_map` to run concurrently. |
| `parallel_map(fn, items)` | Apply `fn` (returning an `agent(...)` spec) over `items` and run all specs in parallel. |
| `output(text)` | Emit a workflow output block. Print-like: one per call, all are preserved. |
| `checkpoint(question)` | Pause the workflow and wait for a human answer via `starloom checkpoint answer`. |
| `fail(reason)` | Abort the workflow with an error. |
| `param(name, *, type, default)` | Declare a parameter (supplied via `-p KEY=VALUE`). |

Example of parallel fan-out:

```python
def main():
    results = parallel_map(
        lambda topic: agent("Write one sentence about " + topic, flags="--model haiku"),
        ["cats", "dogs", "birds"],
    )
    output("\n".join(results))

main()
```

See `examples/` for more: pipelines, refinement loops, review workflows, nested sub-workflows.

---

## CLI at a glance

```
starloom session create <file.star> [-p K=V ...]   Run a workflow
starloom session attach [SESSION_ID]               Watch live or replay
starloom session resume [SESSION_ID]               Restart a stopped session
starloom session list [--status ...]               List sessions
starloom session stop [SESSION_ID]                 Halt a running session
starloom session delete SESSION_ID | --all         Cleanup

starloom node list [-s SESSION_ID]                 Show the graph
starloom node patch NODE_ID [--prompt "..."] [--flags "..."]   Edit and re-run
starloom node stop NODE_ID                         Halt one running node

starloom checkpoint answer CHECKPOINT_ID "text"    Answer a workflow pause
starloom checkpoint approve CHECKPOINT_ID          Approve a backend tool call
starloom checkpoint reject CHECKPOINT_ID           Reject a backend tool call

starloom explain [TOPIC]                           Built-in concept help
starloom completions {bash|zsh|fish}               Shell completions
```

Session selection for any command that takes an optional `SESSION_ID`:
explicit argument → `$STARLOOM_SESSION` → last-used session.

---

## Development

```bash
git clone https://github.com/toadharvard/starloom.git
cd starloom
uv pip install -e '.[dev]'
uv pip install --group dev

pre-commit install
pytest
ruff check .
mypy starloom
```

---

## License

MIT — see [LICENSE](LICENSE).
