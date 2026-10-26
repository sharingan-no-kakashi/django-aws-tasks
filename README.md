# django-tasks-aws

A Django Tasks backend that stores durable task state in SQL, uses standard SQS
queues for delivery, and runs task handlers in Lambda. Tasks can be committed
alongside business data, inspected through Django admin, and recovered by a
scheduled sweeper.

> **Prototype for personal use only.** This project was built for personal use
> in connection with an accompanying blog post (link to be added). Do not use it
> as a production system.

## Architecture

![Django commits task state to SQL and publishes to SQS after commit. Lambda workers claim and finalize in SQL; a scheduled sweeper recovers unfinished work.](docs/_static/task-system-design.svg)

The database owns task state, attempts, and execution leases. SQS carries task
identities; workers check SQL state before executing. EventBridge Scheduler
invokes a sweeper to recover expired leases and missed publications. Each named
route has its own queue and worker configuration.

See the [architecture guide](docs/architecture.rst) for the execution and recovery
protocol.

## Minimal example

After [configuring the backend](docs/installation.rst), define a task in an
allowed module such as `myapp/tasks.py`:

```python
from django.tasks import task

from myapp.models import Event


@task(queue_name="short")
def record_event(event_id: str) -> dict:
    updated = Event.objects.filter(pk=event_id).update(processed=True)
    return {"event_id": event_id, "found": bool(updated)}
```

Enqueue it in the same database transaction as the business change:

```python
from django.db import transaction

from myapp.models import Event
from myapp.tasks import record_event


with transaction.atomic(using="default"):
    event = Event.objects.using("default").create(kind="signup")
    result = record_event.enqueue(event_id=str(event.pk))
```

The task becomes durable when the outer transaction commits. Use `result.refresh()`
to retrieve its current status and `result.return_value` after success.
The [quick start](docs/quickstart.rst) covers execution and result lookup.

## Installation

Install from a local checkout with the driver extra for your database:

```sh
pip install '/path/to/django-aws-tasks[postgres]'
```

Add `django_tasks_aws` to `INSTALLED_APPS`, configure
`django_tasks_aws.backends.DatabaseSqsBackend` in `TASKS`, supply a worker route
manifest, and run migrations. Include the same importable task modules in
producer and worker artifacts. See [installation](docs/installation.rst) and
[configuration](docs/configuration.rst) for complete settings.

The standalone [example application guide](docs/example-repository.md)
provides a web UI, local PostgreSQL setup, AWS emulation, and Terraform examples.
Reusable infrastructure modules live in [terraform/modules](terraform/modules).

## Supported versions

The prototype's compatibility boundaries are:

| Component | Versions |
| --- | --- |
| Python | CPython 3.12–3.14 |
| Django | 6.1, with dependency range `>=6.1,<7` |
| PostgreSQL | 15+; primary integration target |
| MySQL / MariaDB | 8.4+ / 10.6+; experimental adapters, InnoDB required |
| Oracle | 21c+; experimental adapter |

All coordination uses a writable primary database. SQLite is for isolated unit
tests only. See [database setup](docs/database-backends.rst) for engine-specific
requirements and [testing](docs/testing.rst) for suite coverage.

## Documentation

- [Installation](docs/installation.rst) and [quick start](docs/quickstart.rst)
- [Configuration](docs/configuration.rst) and [backend reference](docs/backend-reference.md)
- [Architecture](docs/architecture.rst) and optional [DAG workflows](docs/workflows.rst)
- [Operations](docs/operations.rst) and [troubleshooting](docs/troubleshooting.rst)
- [Testing](docs/testing.rst) and [API reference](docs/api/index.rst)
- [Releases](docs/release.rst) and [changelog](CHANGELOG.md)

## Limitations

This is a personal prototype and is not suitable for production use.
Delivery is at least once; external effects need business idempotency and an
independent way to resolve ambiguous outcomes. Atomic acceptance requires task
and business data to share the same database transaction.

Task handlers must be synchronous and finish within their worker deadline.
The backend uses fixed, non-renewable leases and standard queues with no ordering
guarantee. It does not provide FIFO queues, priority scheduling, strict
requests-per-second limits, cross-database transactions, or automatic compensation.
The current schema supports fresh installations rather than upgrades from earlier
development schemas.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, checks, and pull requests.
Track bugs and proposed work in [GitHub Issues](https://github.com/sharingan-no-kakashi/django-aws-tasks/issues).
Report vulnerabilities through the [security policy](SECURITY.md).
