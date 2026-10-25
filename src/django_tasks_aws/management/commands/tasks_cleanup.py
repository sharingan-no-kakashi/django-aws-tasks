from django_tasks_aws.management.operator import OperatorCommand
from django_tasks_aws.services.retention import expire_details


class Command(OperatorCommand):
    help = "Redact expired terminal details without deleting accepted task identities."

    def configure_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--dry-run", action="store_true")

    def run(self, backend, options):
        return expire_details(backend, limit=options["limit"], dry_run=options["dry_run"])
