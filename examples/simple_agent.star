"""Simple workflow that calls one agent."""

def main():
    result = call_agent("Say hello in one word", flags="--model haiku")
    output(result)

main()
