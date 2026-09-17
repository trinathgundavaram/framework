"""Req_Stat handling through abstract states (design §6.1).

The real status codes are data (ComplianceRequestStatus.Abstract_State), pending open
question Q-01. Code only ever refers to the abstract states below.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from ..errors import ConfigError, InvalidStatusTransition

S_AWAITING = "S_AWAITING"
S_VALIDATED = "S_VALIDATED"
S_PROMOTED = "S_PROMOTED"
S_EXCEPTION = "S_EXCEPTION"
S_COMPLETE = "S_COMPLETE"
S_COMPLETE_EXCEPTION = "S_COMPLETE_EXCEPTION"
S_NOT_PROVIDED = "S_NOT_PROVIDED"

ABSTRACT_STATES = (S_AWAITING, S_VALIDATED, S_PROMOTED, S_EXCEPTION,
                   S_COMPLETE, S_COMPLETE_EXCEPTION, S_NOT_PROVIDED)
REQUIRED_STATES = tuple(s for s in ABSTRACT_STATES if s != S_VALIDATED)


@dataclass
class StatusModel:
    code_by_state: dict[str, str]
    state_by_code: dict[str, str]
    transitions: set[tuple[str, str]]

    @classmethod
    def load(cls, conn: psycopg.Connection) -> "StatusModel":
        rows = conn.execute(
            "SELECT Req_Stat, Abstract_State FROM ComplianceRequestStatus WHERE Active_Ind = 1").fetchall()
        code_by_state: dict[str, str] = {}
        for r in rows:
            if r["abstract_state"] in code_by_state:
                raise ConfigError(f"More than one active Req_Stat mapped to {r['abstract_state']}")
            code_by_state[r["abstract_state"]] = r["req_stat"]
        missing = [s for s in REQUIRED_STATES if s not in code_by_state]
        if missing:
            raise ConfigError(f"ComplianceRequestStatus has no active value for {missing} (see Q-01)")
        # state_by_code covers inactive values too, so historical rows still resolve
        all_rows = conn.execute("SELECT Req_Stat, Abstract_State FROM ComplianceRequestStatus").fetchall()
        trans = conn.execute("SELECT From_Req_Stat, To_Req_Stat FROM ComplianceRequestStatusTransition").fetchall()
        return cls(code_by_state, {r["req_stat"]: r["abstract_state"] for r in all_rows},
                   {(t["from_req_stat"], t["to_req_stat"]) for t in trans})

    def code(self, state: str) -> str:
        return self.code_by_state[state]

    def state(self, code: str) -> str:
        return self.state_by_code[code]

    def check(self, from_code: str, to_code: str) -> None:
        if from_code == to_code:
            return
        if (from_code, to_code) not in self.transitions:
            raise InvalidStatusTransition(f"Req_Stat {from_code} -> {to_code} is not an allowed transition")
