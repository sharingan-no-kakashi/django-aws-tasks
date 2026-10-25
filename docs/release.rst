========================
Release and verification
========================

A release consists of one source revision, one wheel, one source distribution,
and the AWS images built from those exact distributions. The candidate workflow
builds once, runs the declared compatibility matrix, Terraform checks, installed
consumer/browser checks, disposable AWS acceptance, and capacity/cost checks. It
produces ``verified-release-candidate`` only after all mandatory evidence passes.

Support matrix
==============

The first release targets:

* **Python** ``>=3.12,<3.15`` (CPython 3.12, 3.13, 3.14).
* **Django** ``>=6.1,<7``.
* **PostgreSQL** 15 or newer (CI exercises 15 and 16).
* **AWS** standard SQS, Lambda, EventBridge Scheduler, CloudWatch Logs/Metrics.

Compatibility outside these bounds is not claimed. The full CI matrix remains a
release requirement; merely defining a job is not evidence that every cell
passed.

Initial execution contract
==========================

The first release implements a narrow contract; anything broader is a separate,
independently claimed feature:

#. Synchronous, importable tasks resolved by ``module_path`` at enqueue time.
#. Standard queues with at-least-once delivery; no ordering guarantee.
#. Batch size one per worker invocation, zero batch window.
#. Database-scheduled retries with bounded backoff and jitter.
#. Fixed leases sized to each worker's timeout plus margin; no heartbeat.
#. Named worker routes with distinct queues/functions; the example uses
   ``short`` and ``documents``.
#. Explicit ambiguity handling (retry vs. ``ambiguous_external_outcome`` hold).

Explicit exclusions
===================

The first release does **not** provide:

* Global ordering or cross-database transactions. Static workflow graphs are
  implemented as an optional app; see :doc:`workflows`.
* Exactly-once external effects, or automatic compensation.
* Priority scheduling, coroutine/async handlers, or strict requests-per-second
  limiting (concurrency control only).
* FIFO queues or per-resource ordering.
* Tasks whose execution can outlive the configured worker deadline.
* Automatic resolution of ambiguous downstream outcomes when the provider offers
  no identity/query path.

Candidate gate
==============

The candidate gate uses ``.github/release-policy.json`` to require every Python,
PostgreSQL, dependency-bound, infrastructure, AWS, and capacity suite. The gate
rejects stale/foreign receipts, missing suites, skipped/failed mandatory tests,
and changed distribution bytes.

For a local dry candidate (not a successful release gate):

.. code-block:: console

   uv build --no-sources --out-dir /tmp/task-candidate/dist
   uv run python -m scripts.release_evidence candidate \
     --candidate /tmp/task-candidate/candidate.json --dist /tmp/task-candidate/dist \
     --output /tmp/task-candidate/candidate.json

Publishing
==========

Publishing is a separate action. Establish the PyPI project, configure Trusted
Publishing for ``.github/workflows/publish.yml`` and the ``pypi`` environment,
then run ``Publish verified candidate`` against the verified workflow run ID and
exact source commit. Availability of the package name and successful publication
are not claimed until those distinct external steps have completed.

The full procedure, capacity/cost evidence format, and retained-evidence
requirements are in ``docs/release.md``. Current acceptance status is tracked in
``docs/acceptance-status.md``; requirement assignments live in
``docs/verification-matrix.yaml``.

GitHub maintenance
==================

Contributor instructions are in the repository's ``CONTRIBUTING.md``. Before
relying on release automation, complete ``docs/github-maintenance.md``: it covers
required checks, code owners, release PRs, protected environments, OIDC, and
post-publication recovery. Release Please prepares version/changelog PRs only;
the evidence-gated publish workflow creates the public GitHub release after
verifying published package bytes and clean consumer installations.

Implementation, verification, and publication
=============================================

These are separate milestones. Source code can implement an adapter or feature
before it has passed real-service acceptance. A candidate can pass local checks
before AWS and capacity evidence exists. A verified candidate is publishable,
but it is not a public release until the publication and public-artifact checks
complete.

PostgreSQL is the release qualification target described above. MySQL, MariaDB,
and Oracle adapters are implemented experimental paths; their existence does
not extend the qualified matrix. Read :doc:`database-backends` when evaluating
those engines. The beta status applies to the package as a whole.

What each verification layer establishes
========================================

* Unit checks exercise validation, serialization, and isolated decisions.
* Real-database integration checks exercise transactions, competing claims,
  lease fencing, rollback, recovery, and durable workflow transitions.
* Installed-consumer checks verify that built distributions contain the code
  and migrations needed outside the source checkout.
* Infrastructure and AWS acceptance exercise deployed queue/function wiring,
  permissions, delivery, and failure recovery.
* Capacity experiments measure a declared workload envelope; regional cost
  evidence prices the observed usage using retained inputs.

Each layer answers a different question. Passing mocked delivery checks cannot
replace deployment evidence, and a throughput result for synthetic short tasks
cannot establish capacity for a consumer's longer document workload.

Review a candidate before publishing
====================================

Confirm that the candidate commit, distribution hashes, suite receipts, and
retained reports all describe the same bytes. Review skipped tests and external
acceptance status rather than treating a workflow's presence as proof. If source
or artifacts change, create and verify a new candidate.

Keep the verified wheel and source distribution intact through publication.
The publish workflow validates and uploads those files instead of rebuilding.
After upload, public hash comparison and clean consumer installs establish that
users can obtain the verified distributions. If a post-publication step fails,
follow the repository's recovery procedure; uploading a different file under
the same version is not a recovery strategy.

Build and review the documentation
==================================

From the library checkout, build the same Sphinx source used by Read the Docs:

.. code-block:: console

   make docs

The build writes HTML to ``docs/_build/html`` and treats Sphinx warnings as
failures. Open ``docs/_build/html/index.html`` to review navigation, code blocks,
and API details. Read the Docs installs the project plus
``docs/requirements.txt`` using ``.readthedocs.yaml``. Keep those dependencies
consistent with the ``docs`` group in ``pyproject.toml``.
