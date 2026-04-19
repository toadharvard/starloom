"""Simple hello workflow for testing."""

PARAMS = [
    param("name", type="string", default="World"),
]

def main():
    output("Hello, " + name + "!")

main()
