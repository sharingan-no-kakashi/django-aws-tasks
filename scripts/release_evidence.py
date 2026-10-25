"""Run when preparing, verifying, or publishing a release candidate.
Bind test evidence to artifact hashes and source identity to reject stale results.
"""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import time
import tomllib
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

from scripts.check_traceability import MATRIX, read_requirements, validate_coverage

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = ("src", "tests", "terraform", "scripts", "docs", ".github")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_digest():
    paths = [
        ROOT / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "README.md",
            "Django tasks design.md",
            "todo-task.md",
        )
    ]
    for name in SOURCE_ROOTS:
        paths.extend(
            path
            for path in (ROOT / name).rglob("*")
            if path.is_file()
            and not any(
                part in {".terraform", "__pycache__", ".pytest_cache", "staticfiles"}
                for part in path.relative_to(ROOT).parts
            )
            and path.suffix not in {".pyc", ".tfstate", ".tfplan"}
            and ".tfvars" not in path.name
        )
    content = [(str(path.relative_to(ROOT)), digest(path)) for path in sorted(set(paths))]
    return hashlib.sha256(json.dumps(content, separators=(",", ":")).encode()).hexdigest()


def candidate_identity(candidate):
    canonical = {key: value for key, value in candidate.items() if key != "candidate_id"}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def create_candidate(dist):
    artifacts = sorted([*dist.glob("*.whl"), *dist.glob("*.tar.gz")])
    if len(artifacts) != 2 or not any(path.suffix == ".whl" for path in artifacts):
        raise ValueError("A candidate requires exactly one wheel and one source distribution")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    candidate = {
        "project": project["name"],
        "version": project["version"],
        "created_at": time.time(),
        "source_digest": source_digest(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "artifacts": {path.name: digest(path) for path in artifacts},
        "run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    return {**candidate, "candidate_id": candidate_identity(candidate)}


def validate_candidate(candidate, dist):
    if candidate_identity(candidate) != candidate["candidate_id"]:
        raise ValueError("Candidate manifest was modified")
    if candidate["source_digest"] != source_digest():
        raise ValueError("Candidate source differs from this checkout")
    if "commit" in candidate:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        if commit != candidate["commit"]:
            raise ValueError("Candidate commit differs from the selected checkout")
    for name, expected in candidate["artifacts"].items():
        path = dist / name
        if path.parent != dist or digest(path) != expected:
            raise ValueError("Candidate distribution bytes differ from the tested artifact")


def report_cases(path):
    document = ET.parse(path).getroot()
    cases = list(document.iter("testcase"))
    if not cases or any(
        case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error")
    ):
        raise ValueError(f"Mandatory report has missing, skipped, or failed cases: {path}")
    if any(int(suite.get("errors", "0")) for suite in document.iter("testsuite")):
        raise ValueError(f"Mandatory report has suite errors: {path}")
    return [
        {"classname": case.get("classname", ""), "name": case.get("name", "")} for case in cases
    ]


def report_receipt(path, candidate):
    cases = report_cases(path)
    suites = list(ET.parse(path).getroot().iter("testsuite"))
    starts, ends = [], []
    for suite in suites:
        if not suite.get("timestamp"):
            raise ValueError(f"Report has no test execution timestamp: {path}")
        started = datetime.fromisoformat(suite.get("timestamp"))
        started = started if started.tzinfo else started.replace(tzinfo=UTC)
        starts.append(started.timestamp())
        ends.append(started.timestamp() + float(suite.get("time", "0")))
    report = {
        "name": path.name,
        "sha256": digest(path),
        "cases": cases,
        "started_at": min(starts),
        "finished_at": max(ends),
    }
    require_report_freshness(report, candidate, time.time())
    return report


def require_report_freshness(report, candidate, recorded_at):
    if report["started_at"] < candidate["created_at"] - 300:
        raise ValueError("Test report predates this candidate")
    if report["finished_at"] > recorded_at + 300 or report["finished_at"] < report["started_at"]:
        raise ValueError("Invalid test execution timestamps")


def receipt(candidate, suite, reports, evidence):
    return {
        "candidate_id": candidate["candidate_id"],
        "suite": suite,
        "created_at": time.time(),
        "source_digest": source_digest(),
        "python": platform.python_version(),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "reports": [report_receipt(path, candidate) for path in reports],
        "evidence": {
            path.name: {"sha256": digest(path), "document": json.loads(path.read_text())}
            for path in evidence
        },
    }


def validate_receipts(candidate, receipts, policy):
    required = set(policy["required_suites"])
    outcomes = {}
    for item in receipts:
        if (
            item["candidate_id"] != candidate["candidate_id"]
            or item["source_digest"] != candidate["source_digest"]
        ):
            raise ValueError("Receipt belongs to another candidate")
        age = time.time() - item["created_at"]
        if (
            age < -300
            or age > policy["maximum_age_hours"] * 3600
            or item["created_at"] < candidate["created_at"]
        ):
            raise ValueError("Receipt is stale or predates the candidate")
        if candidate["run_id"] and item["run_id"] != candidate["run_id"]:
            raise ValueError("Receipt came from another CI run")
        required.discard(item["suite"])
        for report in item["reports"]:
            require_report_freshness(report, candidate, item["created_at"])
            for case in report["cases"]:
                outcomes.setdefault((case["classname"], case["name"]), []).append(True)
    if required:
        raise ValueError(f"Missing required suites: {sorted(required)}")
    return outcomes


def verify(candidate, receipts, policy):
    outcomes = validate_receipts(candidate, receipts, policy)
    failures = validate_coverage(read_requirements(MATRIX), outcomes)
    if failures:
        raise ValueError("\n".join(failures))
    by_suite = {item["suite"]: item for item in receipts}
    for suite, documents in policy.get("required_evidence", {}).items():
        for name in documents:
            if name not in by_suite[suite]["evidence"]:
                raise ValueError(f"Missing {suite} evidence: {name}")
    validate_external_evidence(candidate, by_suite)
    return {
        "candidate_id": candidate["candidate_id"],
        "verified_at": time.time(),
        "status": "passed",
    }


def validate_external_evidence(candidate, by_suite):
    aws = by_suite["aws"]["evidence"]
    cleanup = aws["cleanup.json"]["document"]
    image = aws["image.json"]["document"]
    identity = aws["identity.json"]["document"]
    if cleanup.get("managed_resources_remaining") != 0 or not cleanup.get("applied_deployment"):
        raise ValueError("AWS cleanup is not complete")
    if cleanup.get("run_id") != identity.get("run_id"):
        raise ValueError("Cleanup belongs to another deployment")
    if image.get("artifact_hashes") != candidate["artifacts"] or "@sha256:" not in image.get(
        "image_uri", ""
    ):
        raise ValueError("AWS image did not use the tested candidate distributions")
    capacity = by_suite["capacity"]["evidence"]["capacity-summary.json"]["document"]
    if (
        not capacity.get("passed")
        or not capacity.get("priced")
        or capacity.get("identity") != identity
        or capacity.get("image") != image
    ):
        raise ValueError("Measured capacity and priced usage for this deployment are required")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["candidate", "record", "verify"])
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    parser.add_argument("--suite")
    parser.add_argument("--report", type=Path, action="append", default=[])
    parser.add_argument("--evidence", type=Path, action="append", default=[])
    parser.add_argument("--receipts", type=Path, default=Path("receipts"))
    parser.add_argument("--policy", type=Path, default=ROOT / ".github/release-policy.json")
    parser.add_argument("--output", type=Path, required=True)
    options = parser.parse_args()
    result = perform(options)
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(json.dumps(result, indent=2))


def perform(options):
    if options.action == "candidate":
        return create_candidate(options.dist)
    candidate = json.loads(options.candidate.read_text())
    validate_candidate(candidate, options.dist)
    if options.action == "record":
        if not options.suite or not options.report:
            raise ValueError("Recording requires a suite and its mandatory JUnit reports")
        return receipt(candidate, options.suite, options.report, options.evidence)
    receipts = [json.loads(path.read_text()) for path in options.receipts.rglob("*.receipt.json")]
    return verify(candidate, receipts, json.loads(options.policy.read_text()))


if __name__ == "__main__":
    main()
