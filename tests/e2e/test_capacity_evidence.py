"""Capacity is accepted only from retained observations for this deployment run."""

import json


def test_capacity_and_cost_evidence_is_complete(deployment):
    document = json.loads((deployment.directory / "capacity-summary.json").read_text())
    assert document["passed"] and document["priced"]
    assert document["identity"] == deployment.identity
    assert len(document["experiments"]) >= 6
    for name in ("burst", "sustained"):
        assert any(
            row["name"] == name and row["report"]["acceptance"]["passed"]
            for row in document["experiments"]
        )
