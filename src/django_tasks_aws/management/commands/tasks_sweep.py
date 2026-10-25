"""Bounded audited reconciliation, with a strictly read-only preview."""

import getpass
from uuid import uuid4

from django.core.management.base import CommandError

from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.control import ControlRequest
from django_tasks_aws.services.operator_sweep import sweep


class Command(OperatorCommand):
    help = "Run bounded audited reconciliation, or preview eligibility with --dry-run."

    def configure_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--queue")
        parser.add_argument("--reason", default="Manual bounded reconciliation")
        parser.add_argument("--request-id", default=None)
        parser.add_argument("--actor", default=getpass.getuser())
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--no-dispatch", action="store_true")

    def run(self, backend, options):
        request = ControlRequest(
            request_id=options["request_id"] or str(uuid4()),
            reason=options["reason"],
            actor_reference=options["actor"],
        )
        self.stderr.write(f"request_id={request.request_id}")
        result = sweep(
            backend,
            request,
            queue=options["queue"],
            limit=options["limit"],
            dry_run=options["dry_run"],
            no_dispatch=options["no_dispatch"],
        )
        if any("error" in row for row in result["results"]):
            self.write_output(result, options)
            raise CommandError("Some reconciliation targets failed")
        return result
