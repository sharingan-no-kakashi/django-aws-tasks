"""Domain-specific exceptions raised by the package."""


class DjangoTasksAWSError(Exception):
    """Base class for all package-raised errors."""


class TaskResolutionError(DjangoTasksAWSError):
    """A stored task import path could not be resolved to an approved Task."""


class ConfigurationError(DjangoTasksAWSError):
    """Worker or backend configuration is missing, unsafe, or inconsistent."""


class SubmissionConflict(DjangoTasksAWSError):
    """A deduplication key was reused with different work or routing."""


class PayloadVersionError(DjangoTasksAWSError):
    """A task payload version is unsupported by the current deployment."""


class PermanentFailure(DjangoTasksAWSError):
    """A handler declares a terminal, non-retryable failure."""


class AmbiguousOutcome(DjangoTasksAWSError):
    """A handler reports an external effect whose outcome is uncertain."""


class ControlRejected(DjangoTasksAWSError):
    """An operator action is not eligible for the current task state."""


class ControlConflict(ControlRejected):
    """An operator request was reused with different intent or stale state."""


class ExistingTaskResultExpired(DjangoTasksAWSError):
    """A deduplicated acceptance remains, but its detailed result expired."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"Existing task {task_id} has expired result details")


class InvariantViolation(DjangoTasksAWSError):
    """Coordination history is inconsistent; automatic repair is unsafe."""


class ExecutionBudgetExceeded(BaseException):
    """A local execution deadline expired; treat possible effects as ambiguous."""
