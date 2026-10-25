"""Local execution through the same compatibility and claim service as Lambda."""

import signal
import threading
import time
from contextlib import contextmanager
from typing import cast

from django.core.management.base import BaseCommand, CommandError
from django.tasks import task_backends

from django_tasks_aws.backends import DatabaseSqsBackend
from django_tasks_aws.configuration import require_writer
from django_tasks_aws.decorators import scoped
from django_tasks_aws.exceptions import ConfigurationError
from django_tasks_aws.schemas import WorkerIdentity
from django_tasks_aws.services import reconcile
from django_tasks_aws.services.execution import ExecutionBudget
from django_tasks_aws.services.worker import handle_task


class Command(BaseCommand):
    help = "Execute bounded local work for a named backend and worker route."

    def add_arguments(self, parser):
        parser.add_argument("--backend", default="default")
        parser.add_argument("--queue", required=True)
        parser.add_argument("--limit", type=int, default=1)
        parser.add_argument("--lease", type=int, default=None)

    def handle(self, *args, **options):
        backend = cast(DatabaseSqsBackend, task_backends[options["backend"]])
        route, lease = self.get_worker_profile(backend, options)
        require_writer(backend.database_alias)
        task_ids = reconcile.select_due_task_ids(
            database_alias=backend.database_alias,
            backend_alias=backend.alias,
            queue_name=route.name,
            dispatch_limit=route.dispatch_limit,
            limit=options["limit"],
        )
        for task_id in task_ids:
            decision = execute_local_task(task_id, backend, route, lease)
            self.stdout.write(f"{task_id} -> {decision}")
        self.stdout.write(f"Processed {len(task_ids)} task(s) on {route.name}.")

    def get_worker_profile(self, backend, options):
        route = backend.routes.get(options["queue"])
        if route is None or route.paused:
            raise CommandError("Select a configured, unpaused route")
        lease = route.lease_seconds if options["lease"] is None else options["lease"]
        if not 1 <= options["limit"] <= 1000 or lease < route.timeout_seconds + 5:
            raise CommandError("Limit must be 1–1000; lease must cover timeout plus five seconds")
        if threading.current_thread() is not threading.main_thread():
            raise CommandError("Run tasks_worker in a separate process on its main thread")
        return route, lease


@contextmanager
def enforce_hard_deadline(seconds):
    """Terminate the local command at its deadline, including blocked native code."""
    if threading.current_thread() is not threading.main_thread():
        raise ConfigurationError("Run tasks_worker in its own process on the main thread")
    previous_handler = signal.signal(signal.SIGALRM, signal.SIG_DFL)
    previous_timer = signal.setitimer(signal.ITIMER_REAL, max(0.001, seconds))
    started = time.monotonic()
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0]:
            signal.setitimer(
                signal.ITIMER_REAL,
                max(0.001, previous_timer[0] - (time.monotonic() - started)),
                previous_timer[1],
            )


@scoped(enforce_hard_deadline, "route.timeout_seconds")
def execute_local_task(task_id, backend, route, lease):
    decision = handle_task(
        task_id,
        backend=backend,
        worker=WorkerIdentity(route.name, route.source_queue_arn),
        remaining_seconds=route.timeout_seconds,
        lease_seconds=lease,
        budget=ExecutionBudget.after(route.timeout_seconds),
    )
    return decision
