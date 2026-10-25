from django_tasks_aws.management.operator import RecoveryCommand


class Command(RecoveryCommand):
    help = "Request audited task release; use --dry-run to inspect eligibility."
    action = "release"
