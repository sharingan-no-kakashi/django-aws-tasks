from dataclasses import asdict

from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.inspection import Category, list_tasks


class Command(OperatorCommand):
    help = "Inspect task summaries without publishing or changing task state."

    def configure_arguments(self, parser):
        parser.add_argument("--queue")
        parser.add_argument("--category", choices=list(Category))
        parser.add_argument("--status")
        parser.add_argument("--task-name")
        parser.add_argument("--hold-reason")
        parser.add_argument("--problematic", action="store_true")
        parser.add_argument("--search")
        parser.add_argument("--limit", type=int, default=50)
        parser.add_argument("--offset", type=int, default=0)

    def run(self, backend, options):
        names = (
            "queue",
            "category",
            "status",
            "task_name",
            "hold_reason",
            "problematic",
            "search",
            "limit",
            "offset",
        )
        return asdict(list_tasks(backend, **{name: options[name] for name in names}))
