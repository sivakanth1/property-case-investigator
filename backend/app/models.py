from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base, UTCDateTime, utcnow


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    hcad: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    address: Mapped[str] = mapped_column(String(200))
    normalized_address: Mapped[str] = mapped_column(String(200), index=True)
    zip: Mapped[str | None] = mapped_column(String(16))
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class SourceRecord(Base):
    __tablename__ = "source_records"
    __table_args__ = (UniqueConstraint("resource_id", "source_row_id", name="uq_source_row"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    resource_id: Mapped[str] = mapped_column(String(64), index=True)
    source_row_id: Mapped[str] = mapped_column(String(32))
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str | None] = mapped_column(String(32), index=True)
    violation_id: Mapped[str | None] = mapped_column(String(32))
    raw_json: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime)
    origin: Mapped[str] = mapped_column(String(24))  # live_api | snapshot_file
    complete: Mapped[bool] = mapped_column(Boolean, default=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    observed_min_date: Mapped[str | None] = mapped_column(String(10))
    observed_max_date: Mapped[str | None] = mapped_column(String(10))
    resource_totals_json: Mapped[str] = mapped_column(Text, default="{}")
    error: Mapped[str | None] = mapped_column(Text)


class InvestigationRun(Base):
    __tablename__ = "investigation_runs"
    __table_args__ = (
        Index(
            "uq_one_active_run_per_property",
            "property_id",
            unique=True,
            sqlite_where=text("status IN ('queued','running','reviewing')"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    mode: Mapped[str] = mapped_column(String(32))  # live_model | deterministic_demo
    model: Mapped[str | None] = mapped_column(String(200))
    checkpoint_json: Mapped[str] = mapped_column(Text, default="{}")
    summary: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RunEvent(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("investigation_runs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("investigation_runs.id", ondelete="CASCADE"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(32))
    summary: Mapped[str] = mapped_column(Text)
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    uncertainty: Mapped[str] = mapped_column(Text)
    review_status: Mapped[str] = mapped_column(String(16), default="draft")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class TaskProposal(Base):
    __tablename__ = "task_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("investigation_runs.id", ondelete="CASCADE"), index=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(300))
    action_type: Mapped[str] = mapped_column(String(40))
    case_ids_json: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(8))
    evidence_ids_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    task_key: Mapped[str] = mapped_column(String(300), unique=True)
    action_type: Mapped[str] = mapped_column(String(40))
    case_ids_json: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(String(200))
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)
    priority: Mapped[str] = mapped_column(String(8))
    evidence_ids_json: Mapped[str] = mapped_column(Text)
    last_run_id: Mapped[int | None] = mapped_column(ForeignKey("investigation_runs.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CasePlan(Base):
    """Saved resolution checklist for one case. Reused on later visits so steps do not change between views."""

    __tablename__ = "case_plans"
    __table_args__ = (
        Index("uq_one_active_plan_per_case", "property_id", "case_id", unique=True,
              sqlite_where=text("status = 'active'")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="active")  # active | superseded
    mode: Mapped[str] = mapped_column(String(32))  # live_model | deterministic_demo
    model: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text)
    source_status: Mapped[str | None] = mapped_column(String(64))
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CasePlanStep(Base):
    __tablename__ = "case_plan_steps"

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[int] = mapped_column(ForeignKey("case_plans.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    ordinance: Mapped[str | None] = mapped_column(String(64))
    evidence_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuthSession(Base):
    """Sign-in sessions for accounts stored in Supabase profiles. Only a SHA-256 of the token is kept."""

    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(320))
    full_name: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(24))
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
