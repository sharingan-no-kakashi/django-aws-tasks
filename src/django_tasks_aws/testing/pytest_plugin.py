"""Explicit pytest plugin; importing the library itself does not require pytest."""

import pytest
from django.tasks import task_backends

from .harness import TaskHarness


@pytest.fixture
def task_harness():
    """Use the consumer's backend with automatic publication disabled."""
    backend = task_backends["default"]
    return TaskHarness(backend)
