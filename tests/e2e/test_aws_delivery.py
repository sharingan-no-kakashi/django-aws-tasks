"""Required deployed checks; local runs explicitly skip without a deployment workspace."""

from example_tools.aws_scenarios import (
    duplicate_delivery,
    happy_path,
    rollback,
    scheduled_recovery,
)


def test_real_sqs_routing(deployment):
    happy_path(deployment)


def test_transaction_rollback(deployment):
    rollback(deployment)


def test_real_scheduler_recovery(deployment):
    scheduled_recovery(deployment)


def test_duplicate_sqs_delivery(deployment):
    duplicate_delivery(deployment)
