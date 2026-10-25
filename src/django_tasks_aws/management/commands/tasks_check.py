from django.core.checks import run_checks
from django.core.management.base import CommandError

from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.inspection import list_tasks
from django_tasks_aws.services.invariants import find_violations


class Command(OperatorCommand):
    help = "Read-only configuration and problem-backlog checks."

    def configure_arguments(self, parser):
        parser.add_argument("--fail-on-problems", action="store_true")
        parser.add_argument("--limit", type=int, default=50)

    def run(self, backend, options):
        errors = [
            str(error)
            for error in run_checks(databases=[backend.database_alias])
            if error.is_serious()
        ]
        problems = list_tasks(backend, problematic=True, limit=options["limit"])
        result = {
            "configuration_errors": errors,
            "invariant_violations": find_violations(
                backend.database_alias, backend_alias=backend.alias, limit=options["limit"]
            ),
            "problems": problems.tasks,
            "more_problems": problems.next_offset is not None,
        }
        if (
            errors
            or result["invariant_violations"]
            or (options["fail_on_problems"] and problems.tasks)
        ):
            self.write_output(result, options)
            raise CommandError("Task system checks found problems")
        return result
