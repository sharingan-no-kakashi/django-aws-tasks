"""Shrink generated lifecycle failures against the independent PostgreSQL oracle."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from django.db import transaction
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test

from django_tasks_aws.services import dispatch
from tests.demo_tasks import add
from tests.support.fake_sqs import FakeSqsTransport
from tests.support.state_machine import Machine, Oracle

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


class TaskLifecycleMachine(RuleBasedStateMachine):
    def __init__(self, backend):
        super().__init__()
        self.resources = ExitStack()
        # Each generated example and shrink starts from an empty transaction.
        # Commit/concurrency behavior remains covered by the dedicated race tests.
        self.resources.enter_context(transaction.atomic(using="postgres"))
        self.resources.callback(transaction.set_rollback, True, using="postgres")
        transport = FakeSqsTransport()
        self.resources.enter_context(
            patch.object(dispatch, "get_default_transport", lambda: transport)
        )
        try:
            result = backend.enqueue(add, (1, 2), {})
            self.machine = Machine(backend, transport, Oracle(str(result.id)))
            self.machine.model.assert_database()
        except BaseException:
            self.resources.close()
            raise

    @rule(data=st.data())
    def transition(self, data):
        action = data.draw(st.sampled_from(self.machine.model.choices()), label="action")
        self.machine.apply(action)

    def teardown(self):
        self.resources.close()


def test_generated_task_lifecycles(test_backend):
    test_backend.immediate_dispatch = False
    run_state_machine_as_test(
        lambda: TaskLifecycleMachine(test_backend),
        settings=settings(max_examples=30, stateful_step_count=50, deadline=None),
    )
