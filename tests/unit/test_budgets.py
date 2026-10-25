"""Deadline protection must interrupt local work before an unsafe business effect."""

import pytest

from django_tasks_aws.exceptions import ConfigurationError, ExecutionBudgetExceeded
from django_tasks_aws.services.execution import (
    ExecutionBudget,
    get_remaining_seconds,
    manage_business_budget,
    require_time,
)


def test_local_deadline_terminates_before_late_effect(tmp_path):
    import signal
    import subprocess
    import sys

    marker = tmp_path / "late-effect"
    code = f"""
import time
from pathlib import Path
import django
django.setup()
from django_tasks_aws.management.commands.tasks_worker import enforce_hard_deadline
with enforce_hard_deadline(0.05):
    time.sleep(1)
    Path({str(marker)!r}).write_text('unsafe late effect')
"""
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == -signal.SIGALRM
    assert not marker.exists()
    with pytest.raises(ConfigurationError):
        get_remaining_seconds()


def test_downstream_timeout_cannot_exceed_finalization_allowance():
    with manage_business_budget(ExecutionBudget.after(10)):
        assert 0 < get_remaining_seconds() <= 5
        with pytest.raises(ExecutionBudgetExceeded):
            require_time(6)
