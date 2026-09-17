"""File-resolution decision tables (design §8). Pure function - no I/O."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional


class Action(str, Enum):
    PROMOTE = "PROMOTE"                              # O-1
    PROMOTE_REPLACE = "PROMOTE_REPLACE"              # O-2
    EXCEPTION_NO_DATA = "EXCEPTION_NO_DATA"          # O-3
    EXCEPTION_KEEP_PRIOR = "EXCEPTION_KEEP_PRIOR"    # O-4
    REOPEN_INSERT = "REOPEN_INSERT"                  # X-1
    REOPEN_REPLACE_PENDING = "REOPEN_REPLACE_PENDING"    # X-2
    REOPEN_RESET_APPROVED = "REOPEN_RESET_APPROVED"      # X-3
    REOPEN_RESET_PROMOTED = "REOPEN_RESET_PROMOTED"      # X-4
    REOPEN_REJECT = "REOPEN_REJECT"                  # X-5


@dataclass(frozen=True)
class ActiveReopen:
    apprvl_stat: str          # PENDING_REVIEW | APPROVED
    promotion_stat: str       # NOT_APPLICABLE | PENDING | PROMOTED | FAILED
    override_ty: str


@dataclass(frozen=True)
class ResolutionInput:
    batch_closed: bool
    resolution_ty: Optional[str]      # NEW_FILE | MISSING | None
    has_prior_promoted: bool          # Current_Load_ID is not null
    file_passed: bool                 # FILE_LEVEL rules passed (incl. warnings) and zero-record rule satisfied
    active_reopen: Optional[ActiveReopen] = None


@dataclass(frozen=True)
class ResolutionDecision:
    action: Action
    rule: str
    override_ty: Optional[str] = None


def reopen_type_for(resolution_ty: Optional[str]) -> str:
    """X-1: MISSING -> LATE_ARRIVAL_REOPEN, NEW_FILE -> CORRECTION_REOPEN."""
    if resolution_ty == "NEW_FILE":
        return "CORRECTION_REOPEN"
    if resolution_ty == "MISSING":
        return "LATE_ARRIVAL_REOPEN"
    raise ValueError(f"a closed batch must be resolved, got {resolution_ty!r}")


def decide(inp: ResolutionInput) -> ResolutionDecision:
    if not inp.batch_closed:
        if inp.file_passed:
            if inp.has_prior_promoted:
                return ResolutionDecision(Action.PROMOTE_REPLACE, "O-2")
            return ResolutionDecision(Action.PROMOTE, "O-1")
        if inp.has_prior_promoted:
            return ResolutionDecision(Action.EXCEPTION_KEEP_PRIOR, "O-4")
        return ResolutionDecision(Action.EXCEPTION_NO_DATA, "O-3")

    if not inp.file_passed:
        return ResolutionDecision(Action.REOPEN_REJECT, "X-5")
    ar = inp.active_reopen
    if ar is None:
        return ResolutionDecision(Action.REOPEN_INSERT, "X-1", reopen_type_for(inp.resolution_ty))
    if ar.apprvl_stat == "PENDING_REVIEW":
        return ResolutionDecision(Action.REOPEN_REPLACE_PENDING, "X-2", ar.override_ty)
    if ar.apprvl_stat == "APPROVED" and ar.promotion_stat == "PROMOTED":
        return ResolutionDecision(Action.REOPEN_RESET_PROMOTED, "X-4", ar.override_ty)   # D-47: type kept
    if ar.apprvl_stat == "APPROVED":
        return ResolutionDecision(Action.REOPEN_RESET_APPROVED, "X-3", ar.override_ty)
    raise ValueError(f"unexpected active reopen state {ar}")
