from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.inspection import get_worker_summaries


class Command(OperatorCommand):
    help = "Inspect route configuration and backlog categories."

    def run(self, backend, options):
        return {"workers": get_worker_summaries(backend)}
