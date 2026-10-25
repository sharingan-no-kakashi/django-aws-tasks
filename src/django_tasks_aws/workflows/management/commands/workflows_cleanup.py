"""Bounded redaction of completed workflow inputs."""

from django.core.management.base import BaseCommand, CommandError

from django_tasks_aws.workflows.adapter import get_task_adapter
from django_tasks_aws.workflows.exceptions import WorkflowError
from django_tasks_aws.workflows.services import expire_workflows


class Command(BaseCommand):
    help = "Redact terminal workflow inputs after the configured workflow retention period."

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--limit", type=int)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        adapter = get_task_adapter(options["backend"])
        try:
            count = expire_workflows(
                database_alias=adapter.database_alias,
                backend_alias=options["backend"],
                limit=options["limit"],
                dry_run=options["dry_run"],
            )
        except WorkflowError as error:
            raise CommandError(str(error)) from error
        verb = "Would redact" if options["dry_run"] else "Redacted"
        self.stdout.write(f"{verb} {count} workflow(s).")
