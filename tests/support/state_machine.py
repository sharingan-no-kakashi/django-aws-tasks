"""Seeded transition oracle independent of the SQL predicates under test."""

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from unittest.mock import patch
from uuid import UUID, uuid4

from django.utils import timezone

from django_tasks_aws.models import HoldReason, TaskAttempt, TaskRecord, TaskStatus
from django_tasks_aws.services import claims, control, dispatch, outcomes, reconcile, retention


class Action(StrEnum):
    PUBLISH = "publish"
    CLAIM = "claim"
    SUCCESS = "success"
    RETRY = "retry"
    FAIL = "fail"
    EXPIRE = "expire"
    RECOVER = "recover"
    HOLD = "hold"
    RELEASE = "release"
    REPLAY = "replay"
    RETAIN = "retain"
    ADVANCE = "advance"
    STALE = "stale"


@dataclass
class Oracle:
    task_id: str
    status: str = TaskStatus.PENDING
    attempts: int = 0
    dispatches: int = 0
    held: bool = False
    due: bool = True
    expired: bool = False
    redacted: bool = False
    token: UUID | None = None
    obsolete_tokens: list[UUID] = field(default_factory=list)

    def choices(self):
        common = [Action.CLAIM, Action.STALE]
        match self.status:
            case TaskStatus.PENDING:
                if self.held:
                    return common + [Action.RELEASE]
                if not self.due:
                    return common + [Action.ADVANCE]
                return common + [Action.PUBLISH, Action.HOLD]
            case TaskStatus.RUNNING:
                if self.expired:
                    return common + [Action.RECOVER, Action.SUCCESS]
                return common + [Action.SUCCESS, Action.RETRY, Action.FAIL, Action.EXPIRE]
            case _:
                return common + ([Action.REPLAY, Action.RETAIN] if not self.redacted else [])

    def assert_database(self):
        row = TaskRecord.objects.using("postgres").get(pk=self.task_id)
        assert (row.status, row.attempts_started, row.dispatches_without_claim) == (
            self.status,
            self.attempts,
            self.dispatches,
        )
        assert bool(row.held_at) == self.held
        assert row.lease_token == self.token
        assert row.attempts_started <= row.max_attempts
        assert bool(row.details_expired_at) == self.redacted
        active = TaskAttempt.objects.using("postgres").filter(
            task_id=self.task_id, status="RUNNING"
        )
        assert active.count() == int(self.status == TaskStatus.RUNNING)
        if not self.redacted:
            assert row.attempts.count() == self.attempts
        if self.status in {TaskStatus.SUCCESSFUL, TaskStatus.FAILED}:
            assert row.finished_at is not None


@dataclass
class Machine:
    backend: object
    transport: object
    model: Oracle

    def apply(self, action):
        match action:
            case Action.CLAIM:
                self.claim()
            case Action.PUBLISH:
                self.publish()
            case Action.SUCCESS | Action.RETRY | Action.FAIL:
                self.finalize(action)
            case Action.EXPIRE | Action.ADVANCE:
                self.advance(action)
            case Action.RECOVER:
                self.recover()
            case Action.HOLD:
                self.hold()
            case Action.RELEASE | Action.REPLAY:
                self.control(action)
            case Action.RETAIN:
                self.retain()
            case Action.STALE:
                self.stale()
        self.model.assert_database()

    def claim(self):
        result = claims.claim_task(
            task_id=self.model.task_id,
            queue_name="short",
            worker_name="short",
            lease_seconds=90,
            database_alias="postgres",
        )
        eligible = (
            self.model.status == TaskStatus.PENDING and not self.model.held and self.model.due
        )
        assert bool(result) == eligible
        if eligible:
            self.model.status = TaskStatus.RUNNING
            self.model.attempts += 1
            self.model.dispatches = 0
            self.model.token = result.attempt_id

    def publish(self):
        result = dispatch.publish_task(
            self.model.task_id,
            database_alias="postgres",
            routes=self.backend.routes,
            transport=self.transport,
        )
        if result == dispatch.DispatchResult.DISPATCHED:
            self.model.dispatches += 1

    def finalize(self, action):
        options = dict(
            task_id=self.model.task_id, attempt_id=self.model.token, database_alias="postgres"
        )
        match action:
            case Action.SUCCESS:
                accepted = outcomes.finalize_success(**options, result=3)
                target = TaskStatus.SUCCESSFUL
            case Action.RETRY:
                accepted = outcomes.finalize_retry(
                    **options, retry_at=timezone.now() + timedelta(hours=1)
                )
                target = TaskStatus.PENDING
            case Action.FAIL:
                accepted = outcomes.finalize_failure(**options, error={})
                target = TaskStatus.FAILED
        assert accepted == (not self.model.expired)
        if accepted:
            self.model.obsolete_tokens.append(self.model.token)
            self.model.token = None
            self.model.status = target
            self.model.due = action != Action.RETRY
            if target == TaskStatus.PENDING and self.model.attempts >= 5:
                reconcile.finish_exhausted_pending(database_alias="postgres", limit=10)
                self.model.status = TaskStatus.FAILED

    def advance(self, action):
        past = timezone.now() - timedelta(seconds=1)
        values = (
            {"lease_expires_at": past}
            if action == Action.EXPIRE
            else {"available_at": past, "next_dispatch_at": past}
        )
        TaskRecord.objects.using("postgres").filter(pk=self.model.task_id).update(**values)
        self.model.expired = action == Action.EXPIRE
        self.model.due = True

    def recover(self):
        assert (
            reconcile.recover_expired_attempts(
                database_alias="postgres", policy_catalog=self.backend.policies, limit=10
            )
            == 1
        )
        self.model.obsolete_tokens.append(self.model.token)
        self.model.token = None
        self.model.expired = False
        self.model.status = TaskStatus.FAILED if self.model.attempts >= 5 else TaskStatus.PENDING
        self.model.held = self.model.status == TaskStatus.PENDING

    def hold(self):
        assert outcomes.hold_pending(
            task_id=self.model.task_id,
            database_alias="postgres",
            hold_reason=HoldReason.INCOMPATIBLE_PAYLOAD,
        )
        self.model.held = True

    def control(self, action):
        request = control.ControlRequest(
            str(uuid4()), "seeded recovery", "model-test", effects_reviewed=True
        )
        options = dict(
            backend=self.backend, task_id=self.model.task_id, action=action.value, request=request
        )
        with patch.object(
            dispatch, "publish_task", return_value=dispatch.DispatchResult.NOT_ELIGIBLE
        ):
            result = control.operate(**options)
            repeated = control.operate(**options)
        assert repeated.repeated and repeated.replacement_task_id == result.replacement_task_id
        if action == Action.RELEASE:
            self.model.held = False
        else:
            self.model = Oracle(str(result.replacement_task_id))

    def retain(self):
        TaskRecord.objects.using("postgres").filter(pk=self.model.task_id).update(
            finished_at=timezone.now() - timedelta(days=100)
        )
        retention.expire_details(self.backend, limit=100)
        self.model.redacted = True

    def stale(self):
        token = self.model.obsolete_tokens[-1] if self.model.obsolete_tokens else uuid4()
        assert not outcomes.finalize_success(
            task_id=self.model.task_id,
            attempt_id=token,
            result="obsolete",
            database_alias="postgres",
        )
