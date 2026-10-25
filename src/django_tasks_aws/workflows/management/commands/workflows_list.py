"""Bounded, structured listing of workflow runs."""

import json
from dataclasses import asdict

from django.core.management.base import BaseCommand, CommandError
from django.core.serializers.json import DjangoJSONEncoder

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.inspection import list_runs


class Command(BaseCommand):
    help = "List workflow runs without displaying arguments or results."

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--status")
        parser.add_argument("--name")
        parser.add_argument("--business-reference")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--offset", type=int, default=0)
        parser.add_argument("--json", action="store_true", dest="as_json")

    def handle(self, *args, **options):
        try:
            alias = get_task_adapter(options["backend"]).database_alias
            runs = list_runs(
                database_alias=alias,
                backend_alias=options["backend"],
                status=options["status"],
                name=options["name"],
                business_reference=options["business_reference"],
                limit=options["limit"],
                offset=options["offset"],
            )
        except (WorkflowError, ValueError) as error:
            raise CommandError(str(error)) from error
        data = {"runs": [asdict(run) for run in runs]}
        self.stdout.write(
            json.dumps(data, cls=DjangoJSONEncoder, indent=None if options["as_json"] else 2)
        )
