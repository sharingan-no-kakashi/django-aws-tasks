"""Read-only, redacted deployment findings with stable machine-readable outcomes."""

from dataclasses import asdict, dataclass
from enum import StrEnum

from botocore.exceptions import BotoCoreError, ClientError
from django.db import DatabaseError, connections
from django.db.migrations.executor import MigrationExecutor

from django_tasks_aws.configuration import require_writer
from django_tasks_aws.databases.context import manage_coordination_transaction
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ConfigurationError


class DiagnosticStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Finding:
    target: str
    status: DiagnosticStatus
    finding: str
    next_action: str = ""


def diagnose(backend, *, cloud=False, clients=None):
    findings = [get_database_finding(backend)]
    if cloud:
        findings.extend(get_cloud_findings(backend, clients))
    else:
        findings.append(
            Finding(
                "cloud",
                DiagnosticStatus.UNAVAILABLE,
                "Cloud evidence was not requested",
                "Use --aws with read-only resource permissions",
            )
        )
    status = get_overall_status(findings)
    return {
        "version": 1,
        "backend": backend.alias,
        "status": status,
        "exit_code": {
            DiagnosticStatus.PASSED: 0,
            DiagnosticStatus.FAILED: 1,
            DiagnosticStatus.UNAVAILABLE: 2,
        }[status],
        "findings": [asdict(finding) for finding in findings],
    }


def get_database_finding(backend):
    try:
        return inspect_database(backend)
    except ConfigurationError:
        return Finding(
            "database.writer",
            DiagnosticStatus.FAILED,
            "Writer configuration is unsuitable",
            "Check engine/version, isolation, time zone, and writer privileges",
        )
    except DatabaseError as error:
        return Finding(
            "database.connection",
            DiagnosticStatus.UNAVAILABLE,
            type(error).__name__,
            "Check database address, credentials, TLS, connectivity, and migration privileges",
        )


def get_overall_status(findings):
    statuses = {finding.status for finding in findings}
    if DiagnosticStatus.FAILED in statuses:
        return DiagnosticStatus.FAILED
    if DiagnosticStatus.UNAVAILABLE in statuses:
        return DiagnosticStatus.UNAVAILABLE
    return DiagnosticStatus.PASSED


def get_cloud_findings(backend, clients):
    bindings = backend.options.get("diagnostic_bindings", {})
    for name, route in backend.routes.items():
        yield get_queue_finding(name, route, clients)
        binding = bindings.get(name, {})
        if not binding.get("function_name") or not binding.get("mapping_uuid"):
            yield Finding(
                f"route.{name}.binding",
                DiagnosticStatus.UNAVAILABLE,
                "Function/mapping identities are not configured",
                "Set diagnostic_bindings for this route from deployment outputs",
            )
            continue
        yield get_binding_finding(name, route, binding, clients)


def get_service_client(service, route, clients):
    if clients is not None:
        return clients[service]
    import boto3
    from botocore.config import Config

    return boto3.client(
        service,
        region_name=route.source_queue_arn.split(":")[3],
        config=Config(connect_timeout=1, read_timeout=2, retries={"total_max_attempts": 1}),
    )


def get_queue_finding(name, route, clients):
    target = f"route.{name}.queue"
    try:
        attributes = get_service_client("sqs", route, clients).get_queue_attributes(
            QueueUrl=route.source_queue_url,
            AttributeNames=["QueueArn", "VisibilityTimeout", "FifoQueue", "RedrivePolicy"],
        )["Attributes"]
        valid = (
            attributes.get("QueueArn") == route.source_queue_arn
            and int(attributes.get("VisibilityTimeout", 0)) == route.visibility_timeout_seconds
            and attributes.get("FifoQueue", "false") == "false"
        )
        import json

        redrive = json.loads(attributes.get("RedrivePolicy", "{}"))
        valid = valid and int(redrive.get("maxReceiveCount", 0)) == route.max_receive_count
        return build_resource_result(target, valid, "Queue identity, visibility, redrive, and type")
    except (BotoCoreError, ClientError) as error:
        return build_unavailable_finding(target, error)
    except (ValueError, TypeError):
        return build_resource_result(target, False, "Queue attributes are malformed")


def get_binding_finding(name, route, binding, clients):
    target = f"route.{name}.binding"
    try:
        client = get_service_client("lambda", route, clients)
        function = client.get_function_configuration(FunctionName=binding["function_name"])
        mapping = client.get_event_source_mapping(UUID=binding["mapping_uuid"])
        valid = (
            function.get("Timeout") == route.timeout_seconds
            and function.get("State") == "Active"
            and mapping.get("FunctionArn") == function.get("FunctionArn")
            and mapping.get("EventSourceArn") == route.source_queue_arn
            and mapping.get("BatchSize") == 1
            and mapping.get("MaximumBatchingWindowInSeconds") == 0
            and mapping.get("State") == ("Disabled" if route.paused else "Enabled")
            and mapping.get("FunctionResponseTypes") == ["ReportBatchItemFailures"]
        )
        return build_resource_result(target, valid, "Function timeout/state and batch-one mapping")
    except (BotoCoreError, ClientError) as error:
        return build_unavailable_finding(target, error)


def build_resource_result(target, valid, finding):
    return Finding(
        target,
        DiagnosticStatus.PASSED if valid else DiagnosticStatus.FAILED,
        finding,
        "" if valid else "Compare deployment outputs and restore configured resource settings",
    )


def build_unavailable_finding(target, error):
    code = (
        error.response.get("Error", {}).get("Code", "ClientError")
        if isinstance(error, ClientError)
        else type(error).__name__
    )
    return Finding(
        target,
        DiagnosticStatus.UNAVAILABLE,
        code,
        "Check read-only IAM permissions, credentials, region, and network access",
    )


@scoped(manage_coordination_transaction, "backend.database_alias")
def inspect_database(backend):
    alias = backend.database_alias
    executor = MigrationExecutor(connections[alias])
    if executor.migration_plan(executor.loader.graph.leaf_nodes()):
        return Finding(
            "database.schema",
            DiagnosticStatus.FAILED,
            "Unapplied migrations",
            f"Run migrate --database={alias} before starting workers",
        )
    require_writer(alias)
    return Finding(
        "database.writer",
        DiagnosticStatus.PASSED,
        "Configured schema is migrated and writer checks passed",
    )
