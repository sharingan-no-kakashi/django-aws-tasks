import json
from io import StringIO
from unittest.mock import Mock
from uuid import uuid4

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.utils import timezone

from django_tasks_aws.models import HoldReason, TaskControlEvent, TaskRecord, TaskStatus
from django_tasks_aws.services import dispatch
from tests.support.factories import make_task_record

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(databases=["postgres"], transaction=True)]


@pytest.fixture(autouse=True)
def no_send(monkeypatch):
    sender = Mock()
    monkeypatch.setattr(dispatch, "publish_task", sender)
    return sender


def command(name, *args):
    out, err = StringIO(), StringIO()
    call_command(name, *args, "--json", stdout=out, stderr=err)
    return json.loads(out.getvalue()), err.getvalue()


def test_read_only_commands_do_not_write_or_publish(no_send):
    record = make_task_record()
    for name, args in (
        ("tasks_list", []),
        ("tasks_show", [str(record.id)]),
        ("tasks_workers", []),
        ("tasks_check", []),
    ):
        result, _ = command(name, *args)
        assert result
    assert TaskControlEvent.objects.using("postgres").count() == 0
    record.refresh_from_db()
    assert record.attempts_started == 0 and record.dispatches_without_claim == 0
    no_send.assert_not_called()


def test_recovery_command_is_idempotent_and_json_clean(no_send):
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    args = [
        str(record.id),
        "--reason",
        "checked effects",
        "--request-id",
        "cli-replay",
        "--effects-reviewed",
    ]
    first, err = command("tasks_replay", *args)
    second, _ = command("tasks_replay", *args)
    assert "request_id=cli-replay" in err
    assert second["results"][0]["repeated"]
    assert first["results"][0]["replacement_task_id"] == second["results"][0]["replacement_task_id"]
    assert no_send.call_count == 1


@pytest.mark.parametrize(
    "name", ["tasks_release", "tasks_dispatch", "tasks_replay", "tasks_sweep", "tasks_cleanup"]
)
def test_command_dry_runs_never_publish_or_write(name, no_send):
    if name == "tasks_replay":
        record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    elif name == "tasks_release":
        record = make_task_record(held_at=timezone.now(), hold_reason=HoldReason.AMBIGUOUS_OUTCOME)
    else:
        record = make_task_record()
    args = ["--dry-run"]
    if name not in ("tasks_sweep", "tasks_cleanup"):
        args.extend([str(record.id), "--reason", "preview", "--effects-reviewed"])
    command(name, *args)
    assert TaskRecord.objects.using("postgres").count() == 1
    assert TaskControlEvent.objects.using("postgres").count() == 0
    no_send.assert_not_called()


def test_partial_bulk_failure_reports_each_target_and_nonzero_status():
    record = make_task_record(status=TaskStatus.FAILED, finished_at=timezone.now())
    out = StringIO()
    with pytest.raises(CommandError):
        call_command(
            "tasks_replay",
            str(record.id),
            str(uuid4()),
            "--reason",
            "investigated",
            "--effects-reviewed",
            "--json",
            stdout=out,
            stderr=StringIO(),
        )
    results = json.loads(out.getvalue())["results"]
    assert results[0]["replacement_task_id"] and results[1]["error"]


def test_invalid_id_and_page_limit_are_rejected():
    for name, args in (
        ("tasks_show", ["bad-id"]),
        ("tasks_list", ["--limit", "201"]),
        ("tasks_sweep", ["--queue", "unknown"]),
    ):
        with pytest.raises(CommandError):
            command(name, *args)


def test_sweep_records_audited_dispatch_request(no_send):
    record = make_task_record()
    result, _ = command("tasks_sweep", "--request-id", "operator-sweep")
    assert result["results"][0]["task_id"] == str(record.id)
    event = TaskControlEvent.objects.using("postgres").get()
    assert event.request_id == "operator-sweep" and event.action == "dispatch"
