"""Portable row locking without Oracle's unsupported SQL LIMIT/FOR UPDATE pairing."""

from django.core.exceptions import ObjectDoesNotExist
from django.db import connections
from django.db.models import Model, QuerySet

from django_tasks_aws.exceptions import ConfigurationError


def lock_one[Record: Model](
    records: QuerySet[Record], *, skip_locked: bool = False, only_self: bool = False
) -> Record | None:
    """Lock a query already restricted to one identity; never add a SQL row limit."""
    connection = connections[records.db]
    of = ("self",) if only_self and connection.features.has_select_for_update_of else ()
    try:
        return records.select_for_update(skip_locked=skip_locked, of=of).get()
    except ObjectDoesNotExist:
        return None


def select_bounded_ids(records, limit, *, lock=True):
    """Fetch at most limit identities, skipping locked rows when taking ownership."""
    if not lock:
        return list(records.values_list("pk", flat=True)[:limit])
    identities = records.select_for_update(skip_locked=True).values_list("pk", flat=True)
    if connections[records.db].features.supports_select_for_update_with_limit:
        return list(identities[:limit])
    if connections[records.db].vendor != "oracle":
        raise ConfigurationError("Database adapter needs a bounded locking implementation")
    return select_oracle_locked_ids(identities, limit)


def select_oracle_locked_ids(identities, limit):
    connection = connections[identities.db]
    compiler = identities.query.get_compiler(using=identities.db)
    sql, params = compiler.as_sql()
    with connection.cursor() as cursor:
        # Disable Oracle driver prefetch BEFORE execution: even an ORM iterator
        # with chunk_size=1 can otherwise lock rows beyond the requested batch.
        driver = cursor.cursor.cursor
        driver.prefetchrows = 0
        driver.arraysize = 1
        cursor.execute(sql, params)
        rows = cursor.fetchmany(limit)
        return [row[0] for row in compiler.results_iter([rows])]
