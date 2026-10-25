"""Deterministic randomness for retry/backoff fault tests."""

import random
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def seeded_random(seed: int) -> Iterator[None]:
    """Run a block with the global ``random`` module deterministically seeded."""
    state = random.getstate()
    random.seed(seed)
    try:
        yield
    finally:
        random.setstate(state)
