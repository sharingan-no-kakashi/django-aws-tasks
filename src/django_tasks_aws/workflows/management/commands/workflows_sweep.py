"""Manual workflow recovery using the same coordinator as completion callbacks."""

from django.core.management.base import BaseCommand, CommandError

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.services import reconcile_workflows


class Command(BaseCommand):
    help = "Advance a bounded batch of unfinished workflows."

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--limit", type=int)

    def handle(self, *args, **options):
        adapter = get_task_adapter(options["backend"])
        try:
            count = reconcile_workflows(
                database_alias=adapter.database_alias,
                backend_alias=options["backend"],
                limit=options["limit"],
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error
        self.stdout.write(f"Reconciled {count} workflow(s).")
