from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.inspection import get_task_detail


class Command(OperatorCommand):
    help = "Inspect a task and its retained attempts, control audit, and replay lineage."

    def configure_arguments(self, parser):
        parser.add_argument("task_id")
        parser.add_argument("--sensitive", action="store_true")
        parser.add_argument("--attempts", action="store_true")
        parser.add_argument("--limit", type=int, default=100)

    def run(self, backend, options):
        return get_task_detail(
            backend,
            options["task_id"],
            sensitive=options["sensitive"],
            history_limit=options["limit"],
        )
