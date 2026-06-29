"""Demo module with an intentional bug for dev_loop testing."""


def add(a: int, b: int) -> int:
    # Bug: subtracts instead of adding.
    return a - b
