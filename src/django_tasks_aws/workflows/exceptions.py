"""Workflow definition and preparation errors."""


class WorkflowError(ValueError):
    """A workflow cannot be accepted or prepared."""


class WorkflowConflict(WorkflowError):
    """A submission key was already accepted with a different graph."""


class PreparationError(WorkflowError):
    """A node's saved task or arguments cannot be prepared."""


class WorkflowRecoveryError(WorkflowError):
    """A workflow step cannot be retried or cancelled in its current state."""


class WorkflowNotFound(WorkflowError):
    """A workflow run does not exist on the selected backend and database."""
