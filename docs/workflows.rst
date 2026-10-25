Workflows
=========

Workflows are an optional orchestration layer over existing Django tasks. They
keep their own run, node, and dependency records. Tasks continue to use the
existing backend, queues, worker leases, retries, and result serialization.

Enable the app
--------------

Install both apps in producers, workers, and sweepers, and migrate the database
used by your task backend before enabling workflow submissions::

    INSTALLED_APPS = [
        # ...
        "django_tasks_aws",
        "django_tasks_aws.workflows",
    ]

    # Run against the backend's configured database alias:
    # python manage.py migrate --database=default

Without the workflows app installed, ordinary tasks keep working and no workflow
tables are queried. Workflows currently use ``DatabaseSqsBackend``. All nodes in
one workflow must use the same backend and database; different queues are allowed.

Compose existing tasks
----------------------

Given existing ``@task`` functions ``upload_file``, ``resize_image``,
``extract_text``, and ``publish_document``::

    from django_tasks_aws.workflows import Workflow, step

    upload = step(upload_file, file_id=123)
    image = step(resize_image, upload=upload.output)
    text = step(extract_text, upload=upload.output)
    publish = step(publish_document, image=image.output, text=text.output)

    run = Workflow(upload >> (image & text) >> publish).enqueue(
        submission_key="document:123:v1",
    )

``step()`` wraps a task without executing it. Each call creates a distinct node;
reusing the same node means one execution. ``>>`` creates dependencies, and ``&``
groups branches. Neither operator mutates its operands. Always parenthesize
parallel groups because Python evaluates ``>>`` before ``&``. Use these operators
rather than Python's ``and`` or ``or``; truth-testing a graph raises ``TypeError``.

``(a & b) >> (c & d)`` means both ``c`` and ``d`` wait for both parents. Parallel
nodes become eligible together, subject to available worker capacity and the
configured advancement batch. A chain expression retains all its nodes, so
``Workflow(a >> b >> c)`` includes ``a``, ``b``, and ``c``.

Output references also imply dependencies. The example above can equivalently
be submitted with ``Workflow(publish).enqueue()``: referenced ancestors are
included automatically. Use explicit edges for ordering without passing data.
The combined explicit and inferred graph is checked for cycles before acceptance.

Display names
~~~~~~~~~~~~~

A workflow may carry a display ``name`` and a searchable ``business_reference``,
and each step may carry a display name::

    upload = step(upload_file, file_id=123).named("upload")
    run = Workflow(upload, name="ingest", business_reference="document:123").enqueue(
        submission_key="document:123:v1",
    )

Display names are presentation metadata: they never become part of submission
identity, so renaming a label cannot retarget accepted work, and a task
keyword named ``name`` is still passed to the task function untouched.

Passing results
---------------

Use ``parent.output`` for the complete JSON result, or select dictionary fields
and list indexes::

    child = step(process_image, image_id=parent.output["images"][0]["id"])

References can appear in positional arguments, keyword arguments, lists, and
dictionaries. They are stored using a tagged encoding that cannot be confused
with ordinary user dictionaries. Once every parent succeeds, the coordinator
resolves references and saves concrete child arguments in an ordinary task
record. Retries therefore use the same saved arguments.

Missing fields, invalid indexes, changed task signatures, or arguments exceeding
the backend's 32 KiB inline limit fail node preparation without executing a task.
The backend's 64 KiB result limit still applies. Use database IDs or object-store
keys for larger data. Task context objects are supplied by the existing worker,
never passed between nodes. Saved queue, priority, delay, and context settings
are checked or restored when a waiting node is prepared.

Durability and failure behavior
-------------------------------

Acceptance saves the graph and its initial tasks in one database transaction.
An enclosing application transaction can roll everything back. SQS publication
occurs only after the outer commit. Waiting nodes have no executable task row.

The coordinator is ``workflows.services.advance_workflow()``. Workers request
advancement after task finalization commits. The existing sweeper advances
unfinished workflows after task recovery and before dispatch. A short workflow
row lock and unique node/task associations prevent competing callbacks from
scheduling a child twice. Busy workflows are skipped and retried later.

If a worker dies before requesting advancement, the sweeper discovers the
completed parent. If publication is lost, ordinary task reconciliation repairs
delivery. These mechanisms prevent duplicate scheduling; business execution
retains the task engine's at-least-once and idempotency requirements.

Retries keep descendants waiting. Operator-held tasks make the run ``BLOCKED``;
independent branches can still advance. Permanent failures skip all waiting
descendants, while independent branches finish normally. A run becomes
``SUCCESSFUL`` only when every node succeeds, or ``FAILED`` once all nodes have
settled after a failure. Preparation errors are recorded on the node; execution
errors and attempts remain on its associated task.

A submission key is scoped to the backend. Repeating it with the same saved
definition returns the existing run; changed definitions raise
``WorkflowConflict``. Identity includes node order, edges, task options, and
arguments, rather than attempting graph-isomorphism matching. Build repeated
submissions consistently. Submission identities survive cleanup.

Replay individual tasks does not replace workflow node associations or revive
completed graphs. To rerun a workflow, submit it under a new key. Static graphs, audited failed-step recovery, and cancellation are supported.
Dynamic expansion, conditional branches, and compensation remain unsupported. Persisting a graph does not
freeze Python code: deploy task changes compatibly with active runs.

Status and outputs API
----------------------

Read-only access is available through two public helpers::

    from django_tasks_aws.workflows import get_run_status, get_node_output

    snapshot = get_run_status(run.pk)
    print(snapshot.status)
    for node in snapshot.nodes:
        print(node.name, node.status, node.blocking_reason, node.task_id)

    value = get_node_output(run.pk, "upload")

`get_run_status` returns a bounded snapshot of run and node states, dependencies,
blocking reasons, and current task identities. It omits task arguments and
results, so it is safe for polling and web views. `get_node_output` returns the
stored result of a successful step selected by its ordinal key or unique display name and raises ``WorkflowError`` when the
step has not succeeded or its details have expired. Ordinal keys take precedence over display names; duplicate display names require
an explicit key. An explicit database must match the selected backend. A missing
run raises ``WorkflowNotFound``. ``list_runs()`` accepts limits 1–1000 and offsets
0–100000; larger scans must be partitioned by the caller.

Inspection, recovery, and retention
-----------------------------------

``enqueue()`` returns a ``WorkflowRun`` model bound to the task database::

    run.refresh_from_db()
    print(run.status)
    for node in run.nodes.all():
        print(node.key, node.task_config["path"], node.status, node.task_id)

Commands are available when the optional app is installed::

    python manage.py workflows_list --backend=default --status=RUNNING
    python manage.py workflows_show RUN_UUID --backend=default --json
    python manage.py workflows_sweep --backend=default --limit=100
    python manage.py workflows_cleanup --backend=default --limit=100
    python manage.py workflows_retry RUN_UUID STEP_KEY --reason="..." --dry-run
    python manage.py workflows_cancel RUN_UUID --reason="..." --dry-run

`workflows_list` and `workflows_show` are read-only and omit arguments and
results. `workflows_retry` and `workflows_cancel` use the same audited services
as the admin and accept `--dry-run` for a side-effect-free preview; both are
idempotent under a `--request-id`.

Inspection uses persisted status, which can briefly lag behind task execution
until a callback or reconciliation pass. Arguments and results are omitted from
the inspection command. Task IDs link nodes to existing task inspection tools.

Ordinary task cleanup preserves results and diagnostics belonging to active
workflows. Terminal runs retain required task results and replacement history until workflow
details are redacted; ordinary task cleanup can then expire those records.
Schedule ``workflows_cleanup`` alongside task cleanup to redact terminal node
argument templates, preparation errors, action reasons, actor references, and
audit summaries after workflow retention expires. ``--dry-run`` previews the
bounded eligible count without mutation. Each selected run is rechecked under
lock before redaction; cleanup is resumable. Request hashes and replacement
lineage survive redaction. Recovery of expired or cancelled runs is refused.
Graph structure, task configuration, task IDs, and submission hashes remain.
Active or blocked runs can retain sensitive inputs indefinitely until resolved.

Versioned workflow contract
---------------------------

Workflow state is versioned with the saved graph. ``definition_version`` is
currently ``1``; advancing a run with any other value raises ``WorkflowError``.
The digest covers node order, edges, task options (path, queue, priority,
``run_after``, ``takes_context``), and arguments.

Run and node states
~~~~~~~~~~~~~~~~~~~

A run is ``RUNNING`` (work remains), ``BLOCKED`` (at least one node is held and
cannot advance), ``SUCCESSFUL`` (every node succeeded), or ``FAILED`` (all nodes
settled and at least one failed or was skipped). Cancellation keeps the run
``RUNNING`` until live attempts settle, then records ``CANCELLED``. A node is ``WAITING`` (a parent
has not succeeded), ``READY`` (its task is accepted and pending), ``RUNNING``
(its task is claimed), ``BLOCKED`` (its task is held), ``SUCCESSFUL``,
``FAILED`` (the task failed permanently, or preparation failed), or ``SKIPPED``
(a parent failed or was skipped).

Submission identity
~~~~~~~~~~~~~~~~~~~

``enqueue(submission_key=...)`` is scoped to the backend. The same key with the
same saved definition returns the original run; a different definition raises
``WorkflowConflict``. Identity is the exact saved graph, not an isomorphism
check, so repeated submissions must be built deterministically.

Output references
~~~~~~~~~~~~~~~~~

``node.output`` (with optional index/key selection such as
``node.output["images"][0]["id"]``) creates a tagged argument reference that also
implies an edge. References resolve only after every parent succeeds; a missing
field, wrong type, or bad index fails node preparation without executing a task.

Failure propagation
~~~~~~~~~~~~~~~~~~~

A permanent failure or preparation error marks that node ``FAILED`` and marks
every waiting descendant ``SKIPPED``. Independent branches continue. A held node
marks its run ``BLOCKED`` without skipping its descendants. A run is
``SUCCESSFUL`` only when every node is ``SUCCESSFUL``.

Compatibility rules
~~~~~~~~~~~~~~~~~~~

When a waiting step is prepared, the coordinator restores the saved destination
(queue, priority), ``run_after`` (an absolute scheduling time preserved verbatim
across retries), and ``takes_context``. If the restored task can no longer be
validated (for example its queue has been retired, its context contract changed,
or its import path no longer resolves), the node records a preparation failure
and keeps its saved destination and arguments. Such a failure does not block
other runs in the same sweep.

Ordinary task replay is distinct from workflow recovery: replaying an individual
task replaces that task only and never rewires a workflow node. Workflow
recovery actions (retry and cancellation) are expressed through workflow
services, preserve the accepted graph and arguments, and are documented in
`Workflow recovery <#workflow-recovery>`_.

Workflow recovery
~~~~~~~~~~~~~~~~~

Recovery operations are versioned with the run and are idempotent and audited:

- **Retry** reopens a failed step (and its skipped descendants) using
  successful parent outputs, leaving completed branches untouched. It creates
  replacement work linked to the original task and preserves the original
  operation key. Required inputs must still be within retention.
- **Cancellation** stops new child scheduling and prevents unclaimed associated
  work from starting, while preserving the leases and eventual outcomes of
  attempts already running. Requesting cancellation is distinct from reaching
  the settled cancelled state.

Recovery requires a nonempty request ID (at most 200 characters), reason (2000),
actor reference (200), actor kind (64), and origin (64). Repeating the same ID on
the same run returns the original audited outcome only when action, target,
review decision, reason, and actor metadata match. Changed intent, including
retry versus cancellation, raises ``WorkflowConflict``. Backend/database scope
is checked before looking up an action; missing or wrong-backend runs raise
``WorkflowNotFound``. Dry runs validate eligibility without an audit mutation.

Admin protocol models are read-only on default and custom sites. Recovery needs
both workflow view permission and ``control_taskrecord``. Node arguments/errors
and action diagnostic details require ``view_task_payload``. Recovery forms use
POST and CSRF protection; eligibility is checked again during execution.

Unsupported features
~~~~~~~~~~~~~~~~~~~~

Dynamic expansion, conditional branches, human approval steps, compensation,
strict ordering, and exactly-once external effects remain unsupported. Static
graphs whose steps are known at submission time are the supported scope.

Limits and extension boundary
-----------------------------

Configure positive integer limits in Django settings::

    DJANGO_TASKS_WORKFLOWS = {
        "max_nodes": 100,
        "max_edges": 1000,
        "max_definition_bytes": 1024 * 1024,
        "advancement_batch": 20,
        "sweep_limit": 100,
        "retention_days": 30,
        "minimum_remaining_seconds": 5,
    }

Deadline-bearing callbacks defer before entering coordination when less than
``minimum_remaining_seconds`` remains. They use engine coordination limits and
leave SQS publication to the sweeper, so commit callbacks perform no network
publication. Sweep telemetry reports the bounded number visited rather than an
unbounded backlog count. Per-statement limits do not establish a whole-operation
deadline on every engine; slow-query/transport and cross-engine timing acceptance
remain open under REM-007. Configure the reserve conservatively.

Each advancement creates at most ``advancement_batch`` tasks. Subsequent sweeps
schedule the rest. Reconciliation visits least recently checked runs first and
isolates per-run database or definition errors so one broken run does not block
others. Keep limits small enough for the worker and sweeper execution budgets.

Graph composition, argument encoding, workflow models, and coordination do not
import the AWS backend. ``adapter.TaskAdapter`` defines task description,
transactional enqueue, database selection, and task-state snapshots. The default
``aws.AwsTaskAdapter`` implements that boundary using the existing public backend
API. Generic lifecycle and retention signals provide the only changes in the
original task engine. A future adapter can be selected through
``DJANGO_TASKS_WORKFLOW_ADAPTER``; it must provide the same transactional durability
and wire equivalent completion, recovery, and result-retention integration.

Walk through a fan-out and join
-------------------------------

In the document example, only ``upload`` is executable immediately. ``image``
and ``text`` wait because their arguments reference its output. Once upload
succeeds, the coordinator resolves those references and creates two ordinary
tasks. They may execute concurrently on their configured routes. ``publish``
waits until both succeed, then receives their saved JSON results.

If text extraction retries, publishing remains waiting while the retry uses its
original resolved input. If text extraction fails permanently, publishing is
skipped. A successful image branch is retained. If extraction is held for an
ambiguous outcome, the workflow becomes blocked while independent eligible work
can still proceed. Inspect the associated task to understand the hold; the graph
alone cannot determine whether a provider effect happened.

This behavior makes graph structure a coordination tool rather than a resource
reservation. Two ready siblings are eligible together, but route concurrency,
worker availability, and advancement batches determine when they actually run.

Choose submission and operation identities
------------------------------------------

A workflow submission key identifies one accepted graph. Each task's operation
key identifies its business effect. For example, ``document:123:v1`` can identify
an ingestion run while a publish task uses a stable provider operation identity.
Retrying a failed workflow step preserves its operation identity; this helps
avoid treating recovery as a new publication.

When submitting a new graph after a business change, choose a new workflow key
and deliberately decide whether its remote operation is new too. A new workflow
key alone does not establish that a repeated external action is safe.

Design outputs for waiting children
-----------------------------------

Prefer small, explicit result objects such as ``{"object_key": "...", "version": 1}``
over transferring document bodies through SQL. Keep those objects readable by
all task versions that may consume them. Output references resolve when a child
is prepared, so changing a parent result's shape can fail a waiting child even
when the parent completed successfully.

An object-store key also needs its own retention policy: retaining a workflow's
SQL result does not preserve the referenced object. Keep external inputs and
parent outputs available for the full execution and recovery window.

Operational sequence for a blocked run
--------------------------------------

#. Inspect the run with ``workflows_show`` and identify the blocked or failed node.
#. Inspect its task and attempts with ``tasks_show``; investigate remote outcomes
   using the operation key before choosing a recovery action.
#. For a held task, use the eligible task release procedure. For a failed step,
   preview ``workflows_retry`` so the workflow service can preserve associations
   and reopen skipped descendants correctly.
#. Apply the reviewed action with a stable request ID and poll the run again.
#. If stopping the run, preview and request ``workflows_cancel``; wait for live
   attempts to settle before interpreting the run as cancelled.

Ordinary task replay is not a substitute for workflow retry. Use the workflow
control path when the goal is to continue the accepted graph.
