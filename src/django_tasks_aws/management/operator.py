"""Consistent command parsing, JSON contracts, and domain error translation."""

import getpass
import json
from dataclasses import asdict, is_dataclass
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError
from django.tasks import task_backends

from django_tasks_aws.exceptions import DjangoTasksAWSError
from django_tasks_aws.services.control import ControlRequest, operate


class OperatorCommand(BaseCommand):
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--json", action="store_true", dest="as_json")
        self.configure_arguments(parser)

    def configure_arguments(self, parser):
        pass

    def handle(self, *args, **options):
        try:
            result = self.run(task_backends[options["backend"]], options)  # type: ignore[attr-defined]
        except (DjangoTasksAWSError, DatabaseError, ValueError, KeyError) as error:
            raise CommandError(str(error)) from error
        self.write_output(result, options)

    def write_output(self, result, options):
        if is_dataclass(result):
            result = asdict(result)  # type: ignore[arg-type]
        self.stdout.write(
            json.dumps(result, cls=DjangoJSONEncoder, indent=None if options["as_json"] else 2)
        )


class RecoveryCommand(OperatorCommand):
    action = ""

    def configure_arguments(self, parser):
        parser.add_argument("task_ids", nargs="+")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--request-id", default=None)
        parser.add_argument("--actor", default=getpass.getuser())
        parser.add_argument("--queue", default=None)
        parser.add_argument("--expected-hold", default=None)
        parser.add_argument("--effects-reviewed", action="store_true")
        parser.add_argument("--reset-delivery", action="store_true")
        parser.add_argument("--dry-run", action="store_true")

    def run(self, backend, options):
        if len(options["task_ids"]) > 200:
            raise CommandError("At most 200 targets are allowed")
        request_id = options["request_id"] or str(uuid4())
        self.stderr.write(f"request_id={request_id}")
        request = build_control_request(options, request_id)
        results = [
            operate_one(backend, task_id, self.action, request, options["dry_run"])
            for task_id in options["task_ids"]
        ]
        if any("error" in result for result in results):
            self.write_output({"results": results}, options)
            raise CommandError("One or more targets were rejected; see per-target results")
        return {"results": results}


def build_control_request(options, request_id):
    return ControlRequest(
        request_id=request_id,
        reason=options["reason"],
        actor_reference=options["actor"],
        queue_name=options.get("queue"),
        expected_hold=options.get("expected_hold"),
        effects_reviewed=options["effects_reviewed"],
        reset_delivery=options["reset_delivery"],
    )


def operate_one(backend, task_id, action, request, dry_run):
    try:
        return asdict(
            operate(
                backend=backend, task_id=task_id, action=action, request=request, dry_run=dry_run
            )
        )
    except (DjangoTasksAWSError, DatabaseError, ValueError) as error:
        return {"task_id": task_id, "request_id": request.request_id, "error": str(error)}
