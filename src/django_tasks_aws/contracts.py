"""Artifact-identified task contracts and bounded read-only backlog compatibility checks."""

import hashlib
import importlib
import inspect
from pathlib import Path

from django.apps import apps
from django.tasks import Task

from django_tasks_aws import __version__
from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.models import TaskRecord
from django_tasks_aws.serialization import dumps

MAX_CONTRACT_TASKS = 1000


def build_contract_manifest(backend, *, artifact):
    artifact = Path(artifact)
    if not artifact.is_file():
        raise ConfigurationError("Provide a local application distribution artifact")
    entries = []
    for module_path in backend.allowed_task_modules:
        module = importlib.import_module(module_path)
        for name, task in vars(module).items():
            if isinstance(task, Task) and not name.startswith("_"):
                entries.append(build_task_contract(backend, task, f"{module_path}.{name}"))
                if len(entries) > MAX_CONTRACT_TASKS:
                    raise ConfigurationError("Task contract exceeds the 1000-task limit")
    manifest = {
        "version": 1,
        "library_version": __version__,
        "backend": backend.alias,
        "artifact_name": artifact.name,
        "artifact_sha256": compute_file_digest(artifact),
        "workflow_versions": [1] if apps.is_installed("django_tasks_aws.workflows") else [],
        "tasks": sorted(entries, key=lambda entry: entry["path"]),
        "visibility": (
            "Declared modules and source fingerprints only; "
            "run in the installed artifact environment"
        ),
    }
    manifest["manifest_sha256"] = hashlib.sha256(dumps(manifest).encode()).hexdigest()
    return manifest


def compute_file_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build_task_contract(backend, task, path):
    backend.validate_task(task)
    policy = backend.policies.get(path)
    source = inspect.getsourcefile(task.func)
    return {
        "path": path,
        "payload_versions": list(policy.payload_versions),
        "routes": list(policy.allowed_routes),
        "takes_context": task.takes_context,
        "source_sha256": compute_file_digest(source) if source else None,
    }


def compare_contract(backend, manifest, *, limit=200):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ConfigurationError("Contract comparison limit must be 1–1000")
    validate_manifest(backend, manifest)
    tasks = {task["path"]: task for task in manifest["tasks"]}
    rows = list(
        TaskRecord.objects.using(backend.database_alias)
        .filter(backend_alias=backend.alias, status__in=["PENDING", "RUNNING"])
        .order_by("created_at", "id")
        .values("id", "task_name", "payload_version", "queue_name", "task_options")[: limit + 1]
    )
    findings = [compare_task(row, tasks) for row in rows[:limit]]
    workflows, graph_more = compare_waiting_workflows(backend, manifest, tasks, limit)
    findings.extend(workflows)
    incomplete = len(rows) > limit or graph_more
    status = (
        "incompatible"
        if any(row["status"] == "incompatible" for row in findings)
        else ("incomplete" if incomplete else "compatible")
    )
    return {
        "version": 1,
        "status": status,
        "backend": backend.alias,
        "manifest_sha256": manifest["manifest_sha256"],
        "findings": findings,
        "more_tasks": len(rows) > limit,
        "more_workflow_nodes": graph_more,
        "snapshot": "Read-only observations can change while producers, workers, or recovery run",
    }


def validate_manifest(backend, manifest):
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise ConfigurationError("Unsupported task contract manifest version")
    if manifest.get("backend") != backend.alias:
        raise ConfigurationError("Task contract belongs to another backend")
    declared = manifest.get("manifest_sha256")
    fields = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    if declared != hashlib.sha256(dumps(fields).encode()).hexdigest():
        raise ConfigurationError("Task contract manifest digest does not match its contents")
    if not isinstance(manifest.get("tasks"), list) or len(manifest["tasks"]) > MAX_CONTRACT_TASKS:
        raise ConfigurationError("Invalid task contract inventory")
    seen = set()
    for task in manifest["tasks"]:
        validate_task_contract(task)
        if task["path"] in seen:
            raise ConfigurationError("Duplicate task contract path")
        seen.add(task["path"])


def validate_task_contract(task):
    if not isinstance(task, dict):
        raise ConfigurationError("Task contracts must be objects")
    if not isinstance(task.get("path"), str) or not 1 <= len(task["path"]) <= 512:
        raise ConfigurationError("Invalid task contract path")
    if type(task.get("takes_context")) is not bool:
        raise ConfigurationError("Task contract takes_context must be boolean")
    routes = task.get("routes")
    if (
        not isinstance(routes, list)
        or not routes
        or any(not isinstance(route, str) or not 1 <= len(route) <= 80 for route in routes)
    ):
        raise ConfigurationError("Task contract routes must be a nonempty name list")
    versions = task.get("payload_versions")
    if (
        not isinstance(versions, list)
        or not versions
        or any(type(version) is not int or version < 1 for version in versions)
    ):
        raise ConfigurationError("Task contract payload versions must be positive integers")


def compare_task(row, contracts):
    contract = contracts.get(row["task_name"])
    identity = str(row["id"])
    if contract is None:
        return {"id": identity, "status": "incompatible", "reason": "missing_import"}
    if row["payload_version"] not in contract["payload_versions"]:
        return {"id": identity, "status": "incompatible", "reason": "unsupported_payload"}
    if row["queue_name"] not in contract["routes"]:
        return {"id": identity, "status": "incompatible", "reason": "retired_route"}
    options = row["task_options"]
    if not isinstance(options, dict) or type(options.get("takes_context")) is not bool:
        return {"id": identity, "status": "incompatible", "reason": "invalid_task_options"}
    valid = options["takes_context"] == contract["takes_context"]
    return {
        "id": identity,
        "status": "compatible" if valid else "incompatible",
        "reason": "declared_contract_matches" if valid else "context_changed",
    }


def compare_waiting_workflows(backend, manifest, tasks, limit):
    if not apps.is_installed("django_tasks_aws.workflows"):
        return [], False
    from django_tasks_aws.workflows.models import WorkflowNode

    nodes = list(
        WorkflowNode.objects.using(backend.database_alias)
        .filter(
            run__backend_alias=backend.alias,
            run__status__in=["RUNNING", "BLOCKED"],
            status="WAITING",
        )
        .order_by("run_id", "position")
        .values("id", "task_config", "run__definition_version")[: limit + 1]
    )
    findings = []
    for node in nodes[:limit]:
        config = node["task_config"]
        row = {
            "id": f"workflow-node:{node['id']}",
            "task_name": config["path"],
            "payload_version": 1,
            "queue_name": config["queue_name"],
            "task_options": config,
        }
        finding = compare_task(row, tasks)
        if node["run__definition_version"] not in manifest["workflow_versions"]:
            finding.update(status="incompatible", reason="unsupported_workflow_version")
        findings.append(finding)
    return findings, len(nodes) > limit
