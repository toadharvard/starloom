"""Simple workflow that calls one agent via the pi backend."""

def main():
    result = call_agent(
        "Say hello in one short sentence",
        backend="pi",
        flags="--model codex-lb/gpt-5.4-mini",
    )
    output(result)

main()
