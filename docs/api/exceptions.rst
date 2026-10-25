==========
Exceptions
==========

Handle errors by their meaning
==============================

Acceptance errors happen before a usable new task result is returned.
``ConfigurationError`` indicates settings that must be repaired;
``TaskResolutionError`` indicates an unavailable or disallowed task path;
``SubmissionConflict`` indicates that a key already represents different work.
Repeating the same input cannot resolve those conditions.

``ExistingTaskResultExpired`` carries the original ``task_id``. The identity is
still accepted even though public details have expired, so handle it as a
retained prior operation rather than an invitation to enqueue a duplicate.
Serialization errors are defined in :doc:`serialization`.

Inside a task body, ``PermanentFailure`` declares a terminal failure and
``AmbiguousOutcome`` reports an uncertain effect. Their execution semantics are
described in :doc:`policies`. Operator services use ``ControlRejected`` for
ineligible changes and ``ControlConflict`` for conflicting request intent.
``InvariantViolation`` requires investigation of inconsistent history.

``ExecutionBudgetExceeded`` inherits from ``BaseException`` so a task's routine
``except Exception`` block does not swallow the execution deadline. Let the
execution wrapper classify it; broad exception interception can defeat recovery.

Exception reference
===================

.. automodule:: django_tasks_aws.exceptions
   :members:
   :undoc-members:
   :show-inheritance:
