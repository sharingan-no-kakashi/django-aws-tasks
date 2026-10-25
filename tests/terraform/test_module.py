"""Provider-mocked infrastructure tests include a real runtime manifest round trip."""

import json
import os
import subprocess
from pathlib import Path

import pytest

from django_tasks_aws.schemas import load_route_manifest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TERRAFORM_TESTS") != "1", reason="Run in the mandatory Terraform CI tier"
)


@pytest.fixture(scope="module")
def module_state():
    directory = ROOT / "terraform/modules/task_system"
    subprocess.run(
        ["terraform", "init", "-backend=false", "-input=false", "-lockfile=readonly"],
        cwd=directory,
        check=True,
    )
    result = subprocess.run(
        ["terraform", "test", "-json", "-verbose"],
        cwd=directory,
        text=True,
        capture_output=True,
        check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    return next(record["test_state"] for record in records if "test_state" in record)


def test_generated_manifest_loads_in_runtime(module_state):
    outputs = module_state["outputs"]
    manifest = load_route_manifest(json.loads(outputs["route_manifest_json"]["value"]))
    assert set(manifest) == {"short", "documents"}
    assert manifest["short"].timeout_seconds == 60
    assert manifest["documents"].timeout_seconds == 300
    assert manifest["short"].source_queue_arn != manifest["documents"].source_queue_arn
    assert all(not output["sensitive"] for output in outputs.values())
    assert "password" not in json.dumps(outputs).lower()


def test_worker_iam_is_scoped_to_its_source(module_state):
    resources = {row["address"]: row["values"] for row in module_state["root_module"]["resources"]}
    for name in ("short", "documents"):
        policy = json.loads(resources[f'aws_iam_role_policy.function["{name}"]']["policy"])
        polling = [
            statement
            for statement in policy["Statement"]
            if "sqs:ReceiveMessage" in statement["Action"]
        ]
        assert len(polling) == 1
        assert polling[0]["Resource"] == [resources[f'aws_sqs_queue.source["{name}"]']["arn"]]
        assert all(
            "sqs:SendMessage" not in statement["Action"] for statement in policy["Statement"]
        )
