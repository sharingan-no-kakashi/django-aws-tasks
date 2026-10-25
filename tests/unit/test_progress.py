"""Progress bounds reject malformed, excessive, and out-of-execution updates."""

import pytest

from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.progress import Progress, report_progress


@pytest.mark.parametrize(
    "overrides",
    [
        {"stage": ""},
        {"stage": "x" * 81},
        {"message": "x" * 501},
        {"completed": -1},
        {"completed": True},
        {"total": 2},
        {"completed": 3, "total": 2},
    ],
)
def test_progress_bounds(overrides):
    with pytest.raises(ValueError):
        Progress(**({"stage": "work"} | overrides))


def test_progress_requires_current_execution_context():
    with pytest.raises(ConfigurationError, match="claimed"):
        report_progress(stage="outside")
