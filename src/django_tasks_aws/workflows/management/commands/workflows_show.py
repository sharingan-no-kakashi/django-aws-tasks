"""Inspect a workflow's progress and blocking reasons without exposing arguments."""

import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError, WorkflowNotFound
from django_tasks_aws.workflows.inspection import get_run_status


class Command(BaseCommand):
    help = "Show a workflow's steps, task IDs, dependencies, and blocking reasons."

    def add_arguments(self, parser):
        parser.add_argument("workflow_id")
        parser.add_argument("--backend", default="default")
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            alias = get_task_adapter(options["backend"]).database_alias
            snapshot = get_run_status(
                options["workflow_id"], database_alias=alias, backend_alias=options["backend"]
            )
        except (WorkflowNotFound, WorkflowError, ValueError) as error:
            raise CommandError(str(error)) from error
        data = asdict(snapshot)
        self.stdout.write(
            json.dumps(data, cls=DjangoJSONEncoder, indent=None if options["as_json"] else 2)
        )
