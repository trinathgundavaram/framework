"""Typed views of configuration rows (design §5.1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class RunType:
    run_ty: str
    run_category_cd: str
    sla_days: int
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "RunType":
        return cls(r["run_ty"], r["run_category_cd"], r["sla_days"], r["active_ind"] == 1)


@dataclass(frozen=True)
class XwalkRow:
    project_cd: str
    table_nm: str
    src_cd: str
    run_ty: str
    effective_start_dt: date
    effective_end_dt: Optional[date]
    cmplnc_vrsn: str
    period_strategy_cd: Optional[str]
    lookback_days: Optional[int]
    lookback_weeks: Optional[int]
    schedule_cron_expr: Optional[str]
    business_tz: str
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "XwalkRow":
        return cls(r["project_cd"], r["table_nm"], r["src_cd"], r["run_ty"], r["effective_start_dt"],
                   r["effective_end_dt"], r["cmplnc_vrsn"], r["period_strategy_cd"], r["lookback_days"],
                   r["lookback_weeks"], r["schedule_cron_expr"], r["business_tz"], r["active_ind"] == 1)

    def effective_on(self, d: date) -> bool:
        return self.active and self.effective_start_dt <= d and (self.effective_end_dt is None or d <= self.effective_end_dt)


@dataclass(frozen=True)
class FileConfig:
    cfg_id: int
    project_cd: str
    table_nm: str
    src_cd: str
    src_file_nm_tmplt: str
    project_alias: str
    table_alias: str
    src_alias: str
    src_file_ty: str
    delmtr_cd: Optional[str]
    line_term_cd: Optional[str]
    has_header: bool
    has_trailer: bool
    allow_zero_records: bool
    engine_cd: str
    rules_vld_md: str
    rules_required: bool
    load_exclude_cols: tuple[str, ...]
    s3_src_file_path: str
    src_file_archive_path: str
    s3_quarantine_path: str
    stg_schema_nm: str
    stg_tblnm: str
    core_schema_nm: str
    core_tblnm: str
    sucs_email_notfn_id: Optional[str]
    failr_email_notfn_id: Optional[str]
    notify_channel_cd: Optional[str]
    sns_topic_arn: Optional[str]
    email_subjct_txt: Optional[str]
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "FileConfig":
        excl = tuple(c.strip().lower() for c in (r["load_exclude_col_list"] or "").split(",") if c.strip())
        return cls(r["cfg_id"], r["project_cd"], r["table_nm"], r["src_cd"], r["src_file_nm_tmplt"],
                   r["project_alias"], r["table_alias"], r["src_alias"], r["src_file_ty"].lower(),
                   r["delmtr_cd"], r["line_term_cd"], r["src_file_has_hdr_ind"] == 1,
                   r["src_file_has_trlr_ind"] == 1, r["allow_zero_rcd_ind"] == 1, r["engine_cd"],
                   r["rules_vld_md"], r["is_rules_engine_required"] == 1, excl, r["s3_src_file_path"],
                   r["src_file_archive_path"], r["s3_quarantine_path"], r["stg_schema_nm"], r["stg_tblnm"],
                   r["core_schema_nm"], r["core_tblnm"], r["sucs_email_notfn_id"], r["failr_email_notfn_id"],
                   r["notify_channel_cd"], r["sns_topic_arn"], r["email_subjct_txt"], r["active_ind"] == 1)


@dataclass(frozen=True)
class ExtractPolicy:
    project_cd: str
    table_nm: str
    run_ty: str
    gating_md: str
    period_rules_vld_md: str
    job_ty: str
    job_nm: Optional[str]
    endpoint_url: Optional[str]
    http_method: Optional[str]
    auth_secret_nm: Optional[str]
    call_timeout_sec: int
    max_call_retry_cnt: int
    active: bool

    @classmethod
    def from_row(cls, r: dict) -> "ExtractPolicy":
        return cls(r["project_cd"], r["table_nm"], r["run_ty"], r["extract_gating_md"],
                   r["period_rules_vld_md"], r["extract_job_ty"], r["extract_job_nm"],
                   r["extract_endpoint_url"], r["extract_http_method"], r["auth_secret_nm"],
                   r["call_timeout_sec"], r["max_call_retry_cnt"], r["active_ind"] == 1)


@dataclass(frozen=True)
class JobParam:
    param_nm: str
    param_src_cd: str
    param_val: Optional[str]
    param_seq: int


@dataclass(frozen=True)
class RuleBinding:
    project_cd: str
    table_nm: str
    src_cd: str
    rule_scope_cd: str
    gre_rule_group: str
    gre_rule_variant: str


@dataclass(frozen=True)
class PeriodStrategy:
    code: str
    sql_file_nm: str
    requires_lookback_days: bool
    requires_lookback_weeks: bool
