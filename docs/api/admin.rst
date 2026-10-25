=====
Admin
=====

Registration and permissions
============================

The default Django admin registers protocol models when the application is
installed. Custom sites can use ``register_admin()``. These registrations expose
inspection and audited review actions rather than ordinary model edit forms.

Grant ``view_taskrecord`` for inspection, ``control_taskrecord`` for recovery,
and ``view_task_payload`` separately for sensitive arguments, results, and error
detail. Staff access alone does not imply every permission. These are model-level
permissions across the deployment; applications requiring tenant isolation must
supply their own access boundary.

Recovery shows a per-target preview and requires a signed confirmation within
15 minutes. The service rechecks eligibility when applying the action, so a
preview may become stale as workers progress. Do not restore model editing to
work around a rejected recovery action. See :doc:`../operations` for action
selection, audit identity, and ambiguity review.

Registration reference
======================

.. automodule:: django_tasks_aws.admin
   :members:
