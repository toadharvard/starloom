"""Parallel workflow — runs 3 agents concurrently."""

def main():
    results = parallel_map(
        lambda topic: agent("Write one sentence about " + topic, flags="--model haiku"),
        ["cats", "dogs", "birds"],
    )
    output("\n".join(results))

main()
