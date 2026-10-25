"""Run after the workflow's always-run destroy step, separately from live scenarios."""

import json
import os
from pathlib import Path


def test_applied_stack_destroyed_with_retained_evidence():
    directory = Path(os.environ["TASK_AWS_WORKSPACE"])
    evidence = json.loads((directory / "cleanup.json").read_text())
    assert evidence["applied_deployment"] and evidence["managed_resources_remaining"] == 0
    assert evidence["run_id"] == json.loads((directory / "identity.json").read_text())["run_id"]
    assert list((directory / "evidence").glob("happy-path.json"))
