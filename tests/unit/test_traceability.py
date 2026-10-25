"""Core and optional workflow requirements fail closed on unsupported evidence."""

from copy import deepcopy

import pytest

from scripts.check_traceability import (
    MATRIX,
    read_requirements,
    validate_coverage,
    validate_inventory,
)
from scripts.check_traceability import (
    test_identity as identity_for_test,
)


def test_versioned_inventory_has_executable_assignments():
    assert validate_inventory(read_requirements(MATRIX)) == []


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown", "unsupported", "empty"])
def test_inventory_rejects_invalid_requirements(mutation):
    rows = deepcopy(read_requirements(MATRIX))
    match mutation:
        case "missing":
            rows.pop()
        case "duplicate":
            rows.append(rows[-1])
        case "unknown":
            rows[-1]["id"] = "WFR-999"
        case "unsupported":
            rows[-1]["status"] = "planned"
        case "empty":
            rows[-1]["test_id"] = []
    assert validate_inventory(rows)


@pytest.mark.parametrize("outcomes", [[], [False], [True, False]])
def test_workflow_evidence_rejects_missing_skipped_or_failed_tests(outcomes):
    row = read_requirements(MATRIX)[-1]
    tests = row["test_id"]
    tests = [tests] if isinstance(tests, str) else tests
    evidence = {identity_for_test(test): outcomes for test in tests}
    assert validate_coverage([row], evidence)


def test_unsupported_workflow_version_is_rejected(tmp_path):
    matrix = tmp_path / "matrix.yaml"
    matrix.write_text("version: 1\nworkflow_version: 2\nrequirements: []\n")
    with pytest.raises(ValueError, match="Unsupported"):
        read_requirements(matrix)
