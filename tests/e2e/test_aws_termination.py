"""Actual Lambda service termination, kept separate from injected transaction failures."""

import pytest
from example_tools.aws_faults import abrupt_termination, stopped_route


@pytest.mark.parametrize("mode", ["hard-timeout", "out-of-memory", "after-claim", "after-effect"])
def test_real_termination_and_lease_recovery(deployment, mode):
    abrupt_termination(deployment, mode)


def test_worker_route_outage_recovers(deployment):
    stopped_route(deployment)


def test_stopped_reconciliation_triggers_and_clears_heartbeat_alarm(deployment):
    from example_tools.aws_faults import reconciliation_outage

    reconciliation_outage(deployment)


def test_actual_multi_az_database_failover(deployment):
    from example_tools.aws_faults import database_failover

    database_failover(deployment)
