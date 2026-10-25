from django_tasks_aws.management.operator import RecoveryCommand


class Command(RecoveryCommand):
    help = "Recover one expired attempt through the audited operator service."
    action = "reconcile"
