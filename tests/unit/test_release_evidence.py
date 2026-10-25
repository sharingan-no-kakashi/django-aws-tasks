"""A release must fail closed on foreign, stale, skipped, and changed candidates."""

import json
import time
from datetime import UTC, datetime

import pytest

from scripts.release_evidence import (
    candidate_identity,
    receipt,
    report_cases,
    validate_candidate,
    validate_receipts,
)


def candidate():
    return {
        "candidate_id": "current",
        "source_digest": "source",
        "created_at": time.time() - 100,
        "run_id": "123",
    }


def received():
    return {
        "candidate_id": "current",
        "source_digest": "source",
        "created_at": time.time(),
        "run_id": "123",
        "suite": "aws",
        "reports": [],
        "evidence": {},
    }


@pytest.mark.parametrize(
    "changes",
    [
        {"candidate_id": "old"},
        {"source_digest": "different"},
        {"run_id": "456"},
        {"created_at": time.time() - 8 * 86400},
        {"created_at": time.time() + 3600},
    ],
)
def test_receipt_rejects_foreign_or_stale_evidence(changes):
    with pytest.raises(ValueError):
        validate_receipts(
            candidate(),
            [{**received(), **changes}],
            {"required_suites": ["aws"], "maximum_age_hours": 168},
        )


def test_receipt_requires_every_mandatory_suite():
    with pytest.raises(ValueError, match="Missing required suites"):
        validate_receipts(
            candidate(),
            [received()],
            {"required_suites": ["aws", "capacity"], "maximum_age_hours": 168},
        )


@pytest.mark.parametrize("result", ["<skipped/>", "<failure/>", "<error/>"])
def test_mandatory_report_cannot_hide_failed_or_skipped_cases(tmp_path, result):
    report = tmp_path / "results.xml"
    report.write_text(f'<testsuite><testcase name="required">{result}</testcase></testsuite>')
    with pytest.raises(ValueError):
        report_cases(report)


def test_candidate_rejects_changed_distribution_bytes(tmp_path, monkeypatch):
    import scripts.release_evidence as releases

    wheel = tmp_path / "package.whl"
    wheel.write_bytes(b"tested")
    document = {"source_digest": "source", "artifacts": {wheel.name: releases.digest(wheel)}}
    document["candidate_id"] = candidate_identity(document)
    monkeypatch.setattr(releases, "source_digest", lambda: "source")
    validate_candidate(document, tmp_path)
    wheel.write_bytes(b"changed after verification")
    with pytest.raises(ValueError, match="bytes differ"):
        validate_candidate(document, tmp_path)


def test_record_binds_actual_report_and_does_not_copy_task_payloads(tmp_path, monkeypatch):
    import scripts.release_evidence as releases

    report = tmp_path / "results.xml"
    report.write_text(
        f'<testsuite timestamp="{datetime.now(UTC).isoformat()}">'
        '<testcase classname="tests.required" name="test_done"/></testsuite>'
    )
    monkeypatch.setattr(releases, "source_digest", lambda: "source")
    result = receipt(candidate(), "runtime", [report], [])
    assert result["reports"][0]["sha256"] == releases.digest(report)
    assert result["reports"][0]["cases"][0]["name"] == "test_done"
    assert json.loads(json.dumps(result))["candidate_id"] == "current"


def test_external_evidence_cannot_waive_cleanup_or_candidate_image():
    from scripts.release_evidence import validate_external_evidence

    identity = {"run_id": "run"}
    artifacts = {"tested.whl": "hash"}
    aws = {
        "cleanup.json": {
            "document": {
                "managed_resources_remaining": 0,
                "applied_deployment": True,
                "run_id": "run",
            }
        },
        "identity.json": {"document": identity},
        "image.json": {"document": {"artifact_hashes": artifacts, "image_uri": "image@sha256:abc"}},
    }
    suites = {
        "aws": {"evidence": aws},
        "capacity": {
            "evidence": {
                "capacity-summary.json": {
                    "document": {
                        "passed": True,
                        "priced": True,
                        "identity": identity,
                        "image": aws["image.json"]["document"],
                    }
                }
            }
        },
    }
    validate_external_evidence({"artifacts": artifacts}, suites)
    aws["cleanup.json"]["document"]["managed_resources_remaining"] = 1
    with pytest.raises(ValueError, match="cleanup"):
        validate_external_evidence({"artifacts": artifacts}, suites)
    aws["cleanup.json"]["document"]["managed_resources_remaining"] = 0
    aws["image.json"]["document"]["artifact_hashes"] = {"different.whl": "different"}
    with pytest.raises(ValueError, match="candidate"):
        validate_external_evidence({"artifacts": artifacts}, suites)


def test_old_xml_cannot_be_rebound_to_a_fresh_candidate(tmp_path):
    from scripts.release_evidence import report_receipt

    report = tmp_path / "old.xml"
    report.write_text(
        '<testsuite timestamp="2020-01-01T00:00:00+00:00"><testcase name="passed"/></testsuite>'
    )
    with pytest.raises(ValueError, match="predates"):
        report_receipt(report, candidate())
