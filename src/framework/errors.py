"""Exception hierarchy. Business outcomes are NOT exceptions; these signal control flow or technical failures."""


class FrameworkError(Exception):
    """Base class."""


class ConfigError(FrameworkError):
    """Configuration is missing or invalid."""


class LockTimeout(FrameworkError):
    """An advisory lock could not be acquired in time (retry later)."""


class InvalidStatusTransition(FrameworkError):
    """A Req_Stat move not listed in ComplianceRequestStatusTransition."""


class TechnicalFailure(FrameworkError):
    """Retryable infrastructure failure (DB, rules engine, object store...)."""


class FileRejected(FrameworkError):
    """A file failed a structural pre-check; carries the quarantine event code."""

    def __init__(self, event_ty: str, message: str):
        super().__init__(message)
        self.event_ty = event_ty


class RowCountMismatch(FrameworkError):
    """Core swap appended a different number of rows than were staged."""


class RuleEngineNotConfigured(TechnicalFailure):
    """The GRE entry point is not configured (open question Q-12)."""


class TriggerBlocked(FrameworkError):
    """Extract is not eligible for the requested trigger."""


class TriggerDeferred(FrameworkError):
    """One or more batches of the extract are locked by another process."""


class TriggerCallFailed(FrameworkError):
    """The extract job/API rejected the call or could not be reached."""


class TriggerOutcomeUnknown(FrameworkError):
    """The call may or may not have been accepted; manual reconciliation is required."""
