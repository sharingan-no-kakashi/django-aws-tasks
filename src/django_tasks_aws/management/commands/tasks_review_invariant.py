"""Record an operator's corruption investigation without guessing a data repair."""

from django_tasks_aws.management.operator import RecoveryCommand


class Command(RecoveryCommand):
    action = "invariant_review"
    help = "Audit review of a corrupt task; preserve its state and history for controlled repair."
