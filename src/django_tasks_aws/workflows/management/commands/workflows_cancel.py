"""Audited, idempotent workflow cancellation."""

import json
from dataclasses import asdict
from uuid import uuid4

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.services import cancel_workflow


class Command(BaseCommand):
    help = "Cancel an active workflow without disturbing running attempts."

    def add_arguments(self, parser):
        parser.add_argument("workflow_id")
        parser.add_argument("--backend", default="default")
        parser.add_argument("--reason", required=True)
        parser.add_argument("--request-id", default=None)
        parser.add_argument("--actor", default="cli")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            alias = get_task_adapter(options["backend"]).database_alias
            request_id = options["request_id"] or str(uuid4())
            outcome = cancel_workflow(
                options["workflow_id"],
                request_id=request_id,
                reason=options["reason"],
                actor_reference=options["actor"],
                database_alias=alias,
                backend_alias=options["backend"],
                dry_run=options["dry_run"],
            )
        except (WorkflowError, ValueError) as error:
            raise CommandError(str(error)) from error
        data = asdict(outcome)
        self.stdout.write(
            json.dumps(data, cls=DjangoJSONEncoder, indent=None if options["as_json"] else 2)
        )
