"""Bounded generated sequences retain the seed and shortest failing prefix."""

import random

import pytest

from tests.demo_tasks import add
from tests.support.state_machine import Action, Machine, Oracle

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.mark.parametrize("seed", [1, 7, 42, 20261018])
def test_seeded_database_transition_model(seed, test_backend, fake_transport, monkeypatch):
    from django_tasks_aws.services import dispatch

    test_backend.immediate_dispatch = False
    monkeypatch.setattr(dispatch, "get_default_transport", lambda: fake_transport)
    result = test_backend.enqueue(add, (1, 2), {})
    machine = Machine(test_backend, fake_transport, Oracle(str(result.id)))
    machine.model.assert_database()
    randomizer = random.Random(seed)
    history = []
    for _ in range(80):
        action = randomizer.choice(machine.model.choices())
        history.append(action.value)
        try:
            machine.apply(action)
        except (AssertionError, ValueError) as error:
            pytest.fail(f"seed={seed}, first failing prefix={history!r}: {error}", pytrace=True)
        if machine.model.redacted:
            machine.apply(Action.STALE)
            break
