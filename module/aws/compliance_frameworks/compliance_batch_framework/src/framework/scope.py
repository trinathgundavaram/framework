"""What a step works on: a project, one table group or one table of it, and optionally one run type."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from . import db
from .common import ConfigError, code

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Scope:
    """`tables` = the tables of the table group (or the one table); None = every table of the project(s)."""
    project_cd: Optional[str] = None
    table_group: Optional[str] = None
    table_nm: Optional[str] = None
    run_ty: Optional[str] = None
    tables: Optional[tuple] = None

    @property
    def narrowed(self) -> bool:
        """Only part of a project's files belong to this run."""
        return self.tables is not None or self.run_ty is not None

    def covers(self, project_cd: Optional[str], table_nm: Optional[str] = None, run_ty: Optional[str] = None) -> bool:
        if self.project_cd and code(project_cd) != self.project_cd:
            return False
        if self.tables is not None and table_nm is not None and code(table_nm) not in self.tables:
            return False
        return not (self.run_ty and run_ty is not None and code(run_ty) != self.run_ty)

    def sql(self, alias: str = "", run_type: bool = True) -> tuple[str, list]:
        """(' AND ...' conditions, parameters) on Project_Cd / Table_Nm / Run_Ty of a table or alias."""
        a = f"{alias}." if alias else ""
        parts, params = [], []
        if self.project_cd:
            parts.append(f"{a}Project_Cd = %s")
            params.append(self.project_cd)
        if self.tables is not None:
            if self.tables:
                parts.append(f"{a}Table_Nm IN ({', '.join(['%s'] * len(self.tables))})")
                params.extend(self.tables)
            else:
                parts.append("1 = 0")
        if run_type and self.run_ty:
            parts.append(f"{a}Run_Ty = %s")
            params.append(self.run_ty)
        return "".join(f" AND {p}" for p in parts), params

    def label(self) -> str:
        parts = [f"project {self.project_cd}" if self.project_cd else "every project"]
        if self.table_group:
            parts.append(f"table group {self.table_group} ({', '.join(self.tables) or 'no active table'})")
        if self.table_nm:
            parts.append(f"table {self.table_nm}")
        if self.run_ty:
            parts.append(f"run type {self.run_ty}")
        return ", ".join(parts)


def resolve_scope(conn, project: Optional[str] = None, table_group: Optional[str] = None,
                  table: Optional[str] = None, run_type: Optional[str] = None) -> Scope:
    """The scope of a run. A table group is looked up in ComplianceTable only when one is given."""
    project, table_group, table, run_type = (code(v) or None for v in (project, table_group, table, run_type))
    if (table_group or table) and not project:
        raise ConfigError("a table group or a table needs its project")
    if table_group and table:
        raise ConfigError("give a table group or a table, not both")
    tables = None
    if table:
        tables = (table,)
    elif table_group:
        try:
            rows = conn.execute("SELECT Table_Nm FROM ComplianceTable WHERE UPPER(Project_Cd) = %s "
                                "AND UPPER(Table_Grp_Cd) = %s AND Active_Ind = 1", (project, table_group)).fetchall()
        except Exception as e:  # noqa: BLE001
            raise ConfigError(f"table group {table_group}: ComplianceTable could not be read: {db.error_line(e)}") from None
        tables = tuple(sorted({code(r["table_nm"]) for r in rows}))
        if not tables:
            log.warning("table group %s of project %s has no active table in ComplianceTable: nothing to do",
                        table_group, project)
    return Scope(project, table_group, table, run_type, tables)
