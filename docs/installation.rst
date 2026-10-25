============
Installation
============

Requirements
============

* **Python** ``>=3.12,<3.15`` (CPython 3.12, 3.13, 3.14).
* **Django** ``>=6.1,<7``.
* **PostgreSQL** 15+, **MySQL** 8.4+, **MariaDB** 10.6+, or **Oracle** 21c+
  on a writable primary. MySQL/MariaDB require InnoDB.
  See :doc:`database-backends` for MySQL requirements. SQLite may be used for
  unit tests only and is never a coordination backend.
* **AWS** standard SQS queues (not FIFO), Lambda, and EventBridge Scheduler for
  the deployed execution path.

.. note::

   PostgreSQL is the release qualification target. MySQL, MariaDB, and Oracle
   adapters are implemented experimental paths; see :doc:`database-backends`
   for their evidence requirements. The package remains beta.

Install
=======

.. code-block:: console

   uv add 'django-tasks-aws[postgres]'

or, with ``pip``:

.. code-block:: console

   pip install 'django-tasks-aws[postgres]'

These commands assume the distribution is available from your configured package
index. For a source checkout before publication, install the local project and
selected extra into the consumer environment, for example:

.. code-block:: console

   pip install -e '/path/to/django-aws-tasks[postgres]'

Replace the placeholder with the actual library checkout. A deployed consumer
should use a deliberately built distribution rather than depend on a developer's
editable checkout.

Choose ``django-tasks-aws[mysql]``, ``django-tasks-aws[mariadb]``, or
``django-tasks-aws[oracle]`` for those engines. See :doc:`database-backends`
for engine-specific settings and supported versions.

The package depends on `AWS Lambda Powertools
<https://docs.powertools.aws.dev/lambda/python/>`_ for structured logging and
X-Ray tracing, and on `tenacity <https://tenacity.readthedocs.io/>`_ for bounded
network retries. Both are installed automatically; no extra is required.

Configure Django
================

Add the application and configure the backend:

.. code-block:: python

   INSTALLED_APPS = [
       ...,
       "django_tasks_aws",
   ]

   TASKS = {
       "default": {
           "BACKEND": "django_tasks_aws.backends.DatabaseSqsBackend",
           "QUEUES": ["short", "documents"],
           "OPTIONS": {
               "database_alias": "default",
               "workers": WORKER_ROUTES,
               "allowed_task_modules": ["myapp.tasks"],
           },
       },
   }

``WORKER_ROUTES`` is the machine-readable route manifest produced by the
Terraform deployment and injected at runtime. See :doc:`configuration` for the
full option and policy reference.

For warm Lambda connection reuse, configure the database alias used by the task
backend with Django's persistent-connection settings:

.. code-block:: python

   DATABASES["default"]["CONN_MAX_AGE"] = None
   DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

The handlers initialize Django once at cold start. Django opens the database
connection on first use and retains it across warm invocations in that execution
environment. Invocation boundaries discard obsolete or broken connections;
health checks reconnect an idle connection closed by the database or the network.
Each execution environment has its own connection. A positive ``CONN_MAX_AGE``
sets a maximum connection lifetime; ``0`` retains per-invocation closing.
Credential resolvers must close the connection when credentials change.

Run the migrations:

.. code-block:: console

   python manage.py migrate django_tasks_aws

The package ships its own migrations for the durable task, attempt, and control
records. See :ref:`operations-migrations` for rollout ordering guidance.

Include the same consumer application tasks in every producer and worker
artifact. The worker resolves a stored import path from its own deployed code,
so producer and worker releases must stay compatible.

Next steps
==========

* :doc:`quickstart` — define a task and find the standalone example project.
* :doc:`configuration` — every backend option and policy knob.
* :doc:`architecture` — how the pieces fit together.

Choose a runtime layout
=======================

Install the package into every process that accepts, executes, or reconciles
tasks. A web application needs the backend, task definitions, settings, and
database access. A worker needs the same application code plus its route's SQS
and Lambda configuration. The sweeper needs the backend settings and database
access even when no web request or queue delivery is occurring.

All three roles must agree on backend alias, route names, task import paths,
and database schema. Selecting a different Django database alias does not copy
business data or create a distributed transaction. To accept work atomically
with a business change, write both using the same database and transaction.

The library's Terraform modules create reusable infrastructure components;
they do not package your Django project. The standalone example shows the
consumer-owned image builds, settings injection, and Terraform roots needed to
assemble those pieces. Use it as a deployment reference for your own project.

Verify the installation
=======================

Run these commands from the consuming Django project, with its settings and
secrets available:

.. code-block:: console

   python manage.py check
   python manage.py showmigrations django_tasks_aws --database=default
   python manage.py tasks_workers --backend=default --json
   python manage.py tasks_check --backend=default --json

Replace ``default`` after ``--database`` with the configured SQL alias and after
``--backend`` with the entry in ``TASKS``; these are different namespaces.
``check`` validates Django configuration, while the task commands inspect the
configured backend and durable backlog. A successful startup check alone does
not prove queue permissions, Lambda networking, or actual worker execution.
Complete the :doc:`quickstart` acceptance/execution cycle as well.

Before enabling deployed producers, apply migrations and make compatible task
code available to workers. Configure queue event mappings and schedule the
sweeper; immediate publication alone cannot recover work lost between commit
and queue delivery. For the optional workflow app, follow the additional app
and migration instructions in :doc:`workflows`.

Common setup problems
=====================

* A missing driver means the selected database extra is not installed in that
  runtime artifact. Installing it only in the web environment does not fix a
  worker image.
* An unknown route usually means ``QUEUES`` and the manifest disagree, or a
  task decorator still selects Django's default queue rather than a named route.
* A task resolution error can mean the module is absent from the artifact,
  outside ``allowed_task_modules``, or no longer exposes a Django task.
* An SQLite writer error is expected: use a supported server database for local
  execution and coordination, even if SQLite is convenient for isolated tests.
