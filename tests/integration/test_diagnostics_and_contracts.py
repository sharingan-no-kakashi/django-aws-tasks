"""Deployment preflight has explicit incomplete outcomes and creates no durable work."""

import hashlib
import json
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError
from django.db import DatabaseError

from django_tasks_aws.contracts import build_contract_manifest, compare_contract
from django_tasks_aws.diagnostics import diagnose
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.serialization import dumps
from django_tasks_aws.workflows import Workflow, step
from tests.demo_tasks import add

DB = "postgres"
pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=[DB], transaction=True)]


def test_diagnostics_default_does_not_create_work_or_call_aws(test_backend, monkeypatch):
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: pytest.fail("Unexpected AWS client"))
    result = diagnose(test_backend)
    assert result["status"] == "unavailable"
    assert result["exit_code"] == 2
    assert not TaskRecord.objects.using(DB).exists()


def test_database_diagnosis_redacts_error_details(test_backend, monkeypatch):
    from django_tasks_aws import diagnostics

    def fail(alias):
        raise DatabaseError("password=do-not-disclose")

    monkeypatch.setattr(diagnostics, "require_writer", fail)
    result = diagnose(test_backend)
    assert "do-not-disclose" not in json.dumps(result)
    assert result["findings"][0]["status"] == "unavailable"


def test_denied_cloud_evidence_is_unavailable(test_backend):
    def denied(**kwargs):
        raise ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "secret diagnostic"}},
            "GetQueueAttributes",
        )

    result = diagnose(
        test_backend, cloud=True, clients={"sqs": SimpleNamespace(get_queue_attributes=denied)}
    )
    assert result["status"] == "unavailable"
    assert "secret diagnostic" not in json.dumps(result)
    assert not TaskRecord.objects.using(DB).exists()


def test_queue_drift_is_failed(test_backend):
    client = SimpleNamespace(
        get_queue_attributes=lambda **kw: {"Attributes": {"QueueArn": "wrong"}}
    )
    result = diagnose(test_backend, cloud=True, clients={"sqs": client})
    assert result["status"] == "failed"
    assert result["exit_code"] == 1


def build_manifest(test_backend, tmp_path):
    artifact = tmp_path / "consumer.whl"
    artifact.write_bytes(b"candidate bytes")
    manifest = build_contract_manifest(test_backend, artifact=artifact)
    assert manifest["artifact_sha256"] == hashlib.sha256(b"candidate bytes").hexdigest()
    return manifest


def resign(manifest):
    fields = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    manifest["manifest_sha256"] = hashlib.sha256(dumps(fields).encode()).hexdigest()


def test_contract_compares_core_and_waiting_workflow_without_body_execution(test_backend, tmp_path):
    test_backend.immediate_dispatch = False
    result = test_backend.enqueue(add, (1, 2), {})
    Workflow(step(add, 1, 1) >> step(add, 2, 2)).enqueue()
    manifest = build_manifest(test_backend, tmp_path)
    comparison = compare_contract(test_backend, manifest)
    assert comparison["status"] == "compatible"
    assert len(comparison["findings"]) == 3
    assert TaskRecord.objects.using(DB).get(pk=result.id).attempts_started == 0
    manifest["tasks"] = [entry for entry in manifest["tasks"] if entry["path"] != add.module_path]
    resign(manifest)
    assert compare_contract(test_backend, manifest)["status"] == "incompatible"


def test_contract_reports_bounds_and_invalid_task_options(test_backend, tmp_path):
    test_backend.immediate_dispatch = False
    for number in range(2):
        test_backend.enqueue(add, (number, 1), {})
    manifest = build_manifest(test_backend, tmp_path)
    result = compare_contract(test_backend, manifest, limit=1)
    assert result["status"] == "incomplete" and result["more_tasks"]
    TaskRecord.objects.using(DB).update(task_options={})
    result = compare_contract(test_backend, manifest)
    assert result["status"] == "incompatible"
    assert all(row["reason"] == "invalid_task_options" for row in result["findings"])
