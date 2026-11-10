"""The application database: reports, runs, uploaded files and the audit trail.

SQLite locally and Azure Database for PostgreSQL in the cloud, through one SQLAlchemy URL. The
database holds workflow state only. The facts, manifest, PDFs and review.json stay in storage,
written by the same pipeline the command line uses, so the web app adds no second source of truth
for anything the report says.
"""

import json
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, inspect, select, text,
                        update)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def now() -> datetime:
    """Every timestamp is UTC, so the app and worker agree whatever zone their hosts run in."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Report(Base):
    """One client and quarter: its uploaded inputs, its drafts and its approval."""

    __tablename__ = "reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    client: Mapped[str] = mapped_column(String(200))  # as typed: short code or full name
    quarter: Mapped[str] = mapped_column(String(10))
    client_name: Mapped[str | None] = mapped_column(String(200))  # resolved by discovery
    client_code: Mapped[str | None] = mapped_column(String(40))
    out_name: Mapped[str | None] = mapped_column(String(80))  # "<CODE>_<QUARTER>", the pipeline's folder
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    # Bumped on every reviewer change, so a draft built before the change is known to be stale.
    review_rev: Mapped[int] = mapped_column(Integer, default=0)
    # Archived reports leave the list but keep everything; Restore brings them back.
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Deleted reports keep only this row and their audit trail; their files, drafts and runs are gone.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    runs: Mapped[list["Run"]] = relationship(back_populates="report", order_by="Run.id.desc()")
    files: Mapped[list["InputFile"]] = relationship(back_populates="report", order_by="InputFile.path")


class Run(Base):
    """One pipeline execution: a draft, or a draft followed by finalization."""

    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    kind: Mapped[str] = mapped_column(String(10))  # "draft" or "finalize"
    status: Mapped[str] = mapped_column(String(10), default="queued")  # queued, running, succeeded, failed
    stage: Mapped[str] = mapped_column(String(200), default="Waiting for a worker")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    log: Mapped[list] = mapped_column(JSON, default=list)
    requested_by: Mapped[str] = mapped_column(String(200))
    review_rev: Mapped[int] = mapped_column(Integer, default=0)  # the reviewer changes this run includes
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)
    n_blockers: Mapped[int | None] = mapped_column(Integer)
    n_warnings: Mapped[int | None] = mapped_column(Integer)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    final_pdf: Mapped[str | None] = mapped_column(String(200))  # set when finalization wrote the PDF
    report: Mapped[Report] = relationship(back_populates="runs")


class InputFile(Base):
    """An uploaded source document, with the hash that pins exactly which bytes a run read."""

    __tablename__ = "input_files"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    path: Mapped[str] = mapped_column(String(400))  # relative to the report's inputs folder
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[str] = mapped_column(String(200))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    report: Mapped[Report] = relationship(back_populates="files")


class AuditEvent(Base):
    """Who did what, when. Rows are only ever added."""

    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("reports.id"))
    run_id: Mapped[int | None] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, connect_args=self.connect_args(url), pool_pre_ping=True)
        self.session = sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def connect_args(url: str) -> dict:
        if url.startswith("sqlite"):
            # SQLite is shared between the web thread and the embedded worker thread.
            return {"check_same_thread": False}
        if url.startswith("postgresql"):
            # Without these, a connection the network silently dropped blocks a query forever; with them
            # libpq gives up within about a minute and the caller sees an error it can retry.
            return {"connect_timeout": 10, "keepalives": 1, "keepalives_idle": 30, "keepalives_interval": 10,
                    "keepalives_count": 3}
        return {}

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        # create_all makes missing tables but never adds columns, so a database from an earlier
        # version gets the columns added since then here.
        have = {c["name"] for c in inspect(self.engine).get_columns("reports")}
        with self.engine.begin() as conn:
            for name in ("archived_at", "deleted_at"):
                if name not in have:
                    kind = Report.__table__.c[name].type.compile(self.engine.dialect)
                    conn.execute(text(f"ALTER TABLE reports ADD COLUMN {name} {kind}"))

    def audit(self, report_id: int, actor: str, action: str, run_id: int | None = None, **detail) -> None:
        # The trail must stay readable by any JSON reader, so NaN and infinity are refused, not written.
        json.dumps(detail, allow_nan=False, default=str)
        with self.session() as s:
            s.add(AuditEvent(report_id=report_id, run_id=run_id, actor=actor, action=action, detail=detail))
            s.commit()

    def claim_next_run(self) -> Run | None:
        """Take the oldest queued run, safely even when several workers poll the same database.

        The conditional UPDATE succeeds for exactly one worker; the others see rowcount 0 and move on.
        """
        with self.session() as s:
            run = s.scalars(select(Run).where(Run.status == "queued").order_by(Run.id).limit(1)).first()
            if run is None:
                return None
            claimed = s.execute(update(Run).where(Run.id == run.id, Run.status == "queued")
                                .values(status="running", started_at=now(), stage="Starting"))
            s.commit()
            return s.get(Run, run.id) if claimed.rowcount == 1 else None
