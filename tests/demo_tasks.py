"""Module-level demo tasks for import-resolution and enqueue tests."""

from django.tasks import task


@task(queue_name="short")
def add(a: int, b: int) -> int:
    return a + b


@task(queue_name="documents")
def render(document_id: str) -> str:
    return f"rendered:{document_id}"


@task(queue_name="short")
def record_result(value: int) -> dict:
    return {"value": value}


@task(queue_name="short")
def fail_permanently(value: str) -> str:
    from django_tasks_aws.exceptions import PermanentFailure

    raise PermanentFailure("permanent failure")


@task(queue_name="short")
def fail_transiently(value: str) -> str:
    raise RuntimeError("transient failure")


@task(queue_name="short")
def ambiguous(value: str) -> str:
    from django_tasks_aws.exceptions import AmbiguousOutcome

    raise AmbiguousOutcome("uncertain external effect")


def add_operation_key(args, kwargs):
    return f"add:{args[0]}+{args[1]}"


@task(queue_name="short")
def report_measurement() -> list[bool]:
    from django_tasks_aws.progress import report_progress

    first = report_progress(stage="measuring", completed=1, total=2, message="private measurement")
    second = report_progress(stage="measuring", completed=2, total=2)
    return [first, second]
