from django_tasks_aws.management.operator import RecoveryCommand


class Command(RecoveryCommand):
    help = "Request audited task replay; use --dry-run to inspect eligibility."
    action = "replay"
