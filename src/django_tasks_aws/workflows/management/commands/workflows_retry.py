"""Audited, idempotent retry of a failed workflow step."""

import json
from dataclasses import asdict
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.services import retry_workflow_step


class Command(BaseCommand):
    help = "Retry a failed workflow step and reopen its skipped descendants."

    def add_arguments(self, parser):
        parser.add_argument("workflow_id")
        parser.add_argument("step_key")
        parser.add_argument("--backend", default="default")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--request-id", default=None)
        parser.add_argument("--actor", default="cli")
        parser.add_argument("--effects-reviewed", action="store_true")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            alias = get_task_adapter(options["backend"]).database_alias
            request_id = options["request_id"] or str(uuid4())
            outcome = retry_workflow_step(
                options["workflow_id"],
                options["step_key"],
                request_id=request_id,
                reason=options["reason"],
                actor_reference=options["actor"],
                database_alias=alias,
                backend_alias=options["backend"],
                effects_reviewed=options["effects_reviewed"],
                dry_run=options["dry_run"],
            )
        except (WorkflowError, ValueError) as error:
            raise CommandError(str(error)) from error
        data = asdict(outcome)
        self.stdout.write(
            json.dumps(data, cls=DjangoJSONEncoder, indent=None if options["as_json"] else 2)
        )
