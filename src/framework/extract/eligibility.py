"""Extract eligibility (design §11.1-§11.2, D-38, D-40, D-49). Pure function."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

AUTO = "AUTO"
MANUAL_ONLY = "MANUAL_ONLY"
NOT_ELIGIBLE = "NOT_ELIGIBLE"
STRICT = "STRICT_ALL_PASS"
BEST_EFFORT = "BEST_EFFORT"


@dataclass(frozen=True)
class EligibilityInput:
    gating_md: str
    required: int
    received: int
    waived: int
    rules_stat: str
    failed_rules: tuple[str, ...]
    waived_rules: frozenset[str]
    today: date
    earliest_trigger_dt: date


@dataclass(frozen=True)
class Eligibility:
    code: str
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def compute(inp: EligibilityInput, strict_waiver_auto: bool = False) -> Eligibility:
    if inp.today < inp.earliest_trigger_dt:
        return Eligibility(NOT_ELIGIBLE, f"SLA hold until {inp.earliest_trigger_dt.isoformat()}")
    if inp.required <= 0:
        return Eligibility(NOT_ELIGIBLE, "no sources are required for this period")
    if inp.rules_stat == "PENDING":
        return Eligibility(NOT_ELIGIBLE, "period rules have not been evaluated for the current data")
    if inp.rules_stat == "ERROR":
        return Eligibility(NOT_ELIGIBLE, "period rules failed technically; refresh required")
    rules_clean = inp.rules_stat in ("PASSED", "PASSED_WITH_WARNINGS")
    if inp.received >= inp.required and rules_clean:
        return Eligibility(AUTO, "all sources have data and period rules passed")

    unwaived = tuple(r for r in inp.failed_rules if r not in inp.waived_rules)
    missing = max(inp.required - inp.received, 0)
    unwaived_missing = max(inp.required - inp.received - inp.waived, 0)

    if inp.gating_md == STRICT:
        complete = unwaived_missing == 0
        rules_ok = rules_clean or (inp.rules_stat == "FAILED" and not unwaived)
        if complete and rules_ok:
            code = AUTO if strict_waiver_auto else MANUAL_ONLY
            return Eligibility(code, "eligible through approved waivers "
                               f"({inp.waived} source waiver(s), {len(inp.failed_rules)} rule waiver(s))")
        reasons = []
        if not complete:
            reasons.append(f"{unwaived_missing} source(s) without data or waiver")
        if not rules_ok:
            reasons.append("period rules failed without waiver: " + ", ".join(unwaived))
        return Eligibility(NOT_ELIGIBLE, "; ".join(reasons))

    warnings = []
    if inp.received == 0:
        warnings.append("no source has data (D-49)")
    elif missing:
        warnings.append(f"{missing} of {inp.required} source(s) have no data")
    if inp.rules_stat == "FAILED":
        warnings.append("period rules failed: " + ", ".join(inp.failed_rules))
    return Eligibility(MANUAL_ONLY, "best-effort: manual trigger with acknowledged warnings", tuple(warnings))
