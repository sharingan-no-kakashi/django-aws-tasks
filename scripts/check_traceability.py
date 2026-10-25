"""Run during development and release checks to validate requirement-to-test mappings.
Release checks also require passing test evidence so missing coverage blocks release.
"""

import argparse
import ast
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs" / "verification-matrix.yaml"
EXPECTED_COUNTS = {"INV": 10, "TRN": 9, "ACK": 13, "FM": 33, "DS": 21, "DLV": 8, "WFR": 20}
EXPECTED_IDS = {
    f"{prefix}-{number:03d}"
    for prefix, count in EXPECTED_COUNTS.items()
    for number in range(1, count + 1)
}
REQUIRED_FIELDS = {"id", "source", "title", "layer", "status", "test_id"}


def read_requirements(path: Path) -> list[dict]:
    document = yaml.safe_load(path.read_text())
    if not isinstance(document, dict) or not isinstance(document.get("requirements"), list):
        raise ValueError("Matrix must contain a requirements list")
    if document.get("version") != 1 or document.get("workflow_version") != 1:
        raise ValueError("Unsupported core or workflow requirement version")
    return document["requirements"]


def validate_inventory(requirements: list[dict]) -> list[str]:
    failures = []
    seen = set()
    for requirement in requirements:
        if not isinstance(requirement, dict) or REQUIRED_FIELDS - requirement.keys():
            failures.append(f"Malformed requirement: {requirement!r}")
            continue
        identity = requirement["id"]
        if not isinstance(identity, str):
            failures.append("Requirement id must be a string")
            continue
        if identity in seen:
            failures.append(f"Duplicate requirement: {identity}")
        seen.add(identity)
        failures.extend(validate_assignment(requirement))
    failures.extend(f"Missing requirement: {identity}" for identity in sorted(EXPECTED_IDS - seen))
    failures.extend(f"Unknown requirement: {identity}" for identity in sorted(seen - EXPECTED_IDS))
    return failures


def validate_assignment(requirement):
    identity = requirement["id"]
    failures = []
    if requirement["status"] not in {"implemented", "verified", "passed"}:
        failures.append(f"{identity}: requirement is not implemented")
    tests = requirement["test_id"]
    tests = [tests] if isinstance(tests, str) else tests
    if not isinstance(tests, list) or not tests:
        return [*failures, f"{identity}: test_id not assigned"]
    for test_id in tests:
        try:
            if not isinstance(test_id, str):
                raise ValueError("Test id must be a string")
            test_identity(test_id)
        except (OSError, SyntaxError, ValueError) as error:
            failures.append(f"{identity}: {error}")
    return failures


def read_evidence(paths: list[Path]) -> dict[tuple[str, str], list[bool]]:
    evidence = {}
    for path in paths:
        document = ET.parse(path).getroot()
        for case in document.iter("testcase"):
            identity = (case.get("classname", ""), case.get("name", ""))
            passed = not any(case.find(tag) is not None for tag in ("skipped", "failure", "error"))
            evidence.setdefault(identity, []).append(passed)
        if any(suite.get("errors", "0") != "0" for suite in document.iter("testsuite")):
            raise ValueError(f"Test report contains suite errors: {path}")
    return evidence


def test_identity(test_id: str) -> tuple[str, str]:
    parts = test_id.split("::")
    path = (ROOT / parts[0]).resolve()
    if len(parts) < 2 or not path.is_relative_to(ROOT) or path.suffix != ".py":
        raise ValueError(f"Invalid pytest test id: {test_id}")
    tree = ast.parse(path.read_text())
    function = parts[-1].split("[", 1)[0]
    if not any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function
        for node in ast.walk(tree)
    ):
        raise ValueError(f"Test function does not exist: {test_id}")
    module = ".".join(path.relative_to(ROOT).with_suffix("").parts)
    return ".".join([module, *parts[1:-1]]), parts[-1]


def validate_coverage(requirements: list[dict], evidence: dict) -> list[str]:
    failures = []
    for requirement in requirements:
        identity = requirement["id"]
        tests = requirement["test_id"]
        tests = [tests] if isinstance(tests, str) else tests
        if not isinstance(tests, list) or not tests:
            failures.append(f"{identity}: test_id not assigned")
            continue
        if requirement["status"] not in {"implemented", "verified", "passed"}:
            failures.append(f"{identity}: requirement is not implemented")
        for test_id in tests:
            try:
                if not isinstance(test_id, str):
                    raise ValueError("Test id must be a string")
                outcomes = evidence.get(test_identity(test_id), [])
                if not outcomes or not all(outcomes):
                    failures.append(
                        f"{identity}: missing, skipped, or failed evidence for {test_id}"
                    )
            except (OSError, SyntaxError, ValueError) as error:
                failures.append(f"{identity}: {error}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Require passing release evidence")
    parser.add_argument("--matrix", type=Path, default=MATRIX)
    parser.add_argument("--junit-report", type=Path, action="append", default=[])
    args = parser.parse_args()
    if args.strict and not args.junit_report:
        parser.error("--strict requires --junit-report")
    try:
        requirements = read_requirements(args.matrix)
        failures = validate_inventory(requirements)
        if args.strict and not failures:
            failures.extend(validate_coverage(requirements, read_evidence(args.junit_report)))
    except (OSError, ValueError, yaml.YAMLError, ET.ParseError) as error:
        failures = [str(error)]
    for failure in failures:
        print(failure)
    if failures:
        return 1
    print(
        f"{len(requirements)} requirements valid"
        + (" with passing evidence." if args.strict else ".")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
