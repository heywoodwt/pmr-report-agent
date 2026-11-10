"""The background worker: takes queued runs from the database and runs the unchanged PMR pipeline.

Locally the web app starts this loop on a thread. In Azure it runs as its own Container App with
`python -m pmr_web.worker`, so a two-minute run with live model calls never holds a web request open,
and the worker can scale separately from the reviewer-facing app.

The database is the queue (see Database.claim_next_run). A report run takes seconds to minutes and a
firm produces a few dozen reports a quarter, so polling one table is enough; a message broker would
add a service without removing a problem.
"""

import hashlib
import json
import logging
import tempfile
import threading
import traceback
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select, update

from pmr import deck, pipeline, review
from pmr.llm import Claude
from pmr_web.config import Settings
from pmr_web.db import Database, Report, Run, now
from pmr_web.storage import Storage, open_storage

log = logging.getLogger("pmr_web.worker")

# A run still marked running after this long belongs to a worker that died; it is failed so the
# reviewer can start another.
STALE_AFTER = timedelta(minutes=30)

CACHE_PREFIX = "llm_cache"


@dataclass
class Services:
    settings: Settings
    db: Database
    storage: Storage

    @classmethod
    def start(cls, settings: Settings) -> "Services":
        # The SQLite file and local storage live here; in Azure it holds only the evidence viewer's cache.
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        db = Database(settings.database_url)
        db.create_all()
        services = cls(settings, db, open_storage(settings))
        services.seed_cache()
        return services

    def seed_cache(self) -> int:
        """Copy the committed model answers into storage once, so every worker shares one cache."""
        if self.settings.seed_cache is None:
            return 0
        have = set(self.storage.list(CACHE_PREFIX))
        n = 0
        for path in sorted(self.settings.seed_cache.glob("*.json")):
            key = f"{CACHE_PREFIX}/{path.name}"
            if key not in have:
                self.storage.write(key, path.read_bytes())
                n += 1
        return n


def report_prefix(report_id: int, part: str) -> str:
    return f"reports/{report_id}/{part}"


class Progress:
    """Writes the current stage, a percentage and a log line to the run's database row."""

    def __init__(self, db: Database, run_id: int):
        self.db, self.run_id = db, run_id

    def __call__(self, message: str, stage: bool = True) -> None:
        values = {}
        if stage:
            values["stage"] = message
            if message in pipeline.STAGES:
                # Each stage marks the start of its step, so the bar shows completed steps.
                values["progress"] = round(100 * pipeline.STAGES.index(message) / len(pipeline.STAGES))
        with self.db.session() as s:
            run = s.get(Run, self.run_id)
            run.log = [*run.log, {"at": now().isoformat(timespec="seconds"), "message": message}]
            for k, v in values.items():
                setattr(run, k, v)
            s.commit()


class ProgressClaude(Claude):
    """The pipeline's model client, reporting each answer as it arrives."""

    def __init__(self, cache_dir: Path, enabled: bool, progress: Progress):
        super().__init__(cache_dir, enabled=enabled)
        self.progress = progress

    def json(self, task, content, schema):
        before = dict(self.calls)
        answer = super().json(task, content, schema)
        how = next((k for k in self.calls if self.calls[k] != before[k]), "unknown")
        n = sum(self.calls.values())
        self.progress(f"Model answer {n}: {how}", stage=False)
        return answer


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _export_chart_images(facts: dict, inputs: Path, out_dir: Path) -> None:
    """Save each deck chart image beside the facts, so the review screen can show it without the deck."""
    charts = facts.get("narrative", {}).get("charts", [])
    if not charts or not facts.get("sources", {}).get("deck"):
        return
    slides = {s.number: s for s in deck.load(inputs / facts["sources"]["deck"])}
    folder = out_dir / "charts"
    folder.mkdir(exist_ok=True)
    for ch in charts:
        s = slides.get(ch["slide"])
        # "...#slide4/image1" -> picture index 0 on that slide, as review.page reads it.
        k = int(ch["loc"].rsplit("image", 1)[1]) - 1
        if s and k < len(s.images):
            blob, media = s.images[k]
            ext = {"image/png": "png", "image/jpeg": "jpg", "image/gif": "gif"}.get(media, "png")
            (folder / f"{ch['id']}.{ext}").write_bytes(blob)


def execute(sv: Services, run_id: int) -> None:
    """Run one claimed run end to end and record the outcome. Never raises."""
    progress = Progress(sv.db, run_id)
    with sv.db.session() as s:
        run = s.get(Run, run_id)
        report = run.report
        kind, rid, client, quarter, actor = run.kind, report.id, report.client, report.quarter, run.requested_by
    sv.db.audit(rid, actor, f"{kind}_started", run_id)
    try:
        with tempfile.TemporaryDirectory(prefix="pmr_run_") as tmp:
            tmp = Path(tmp)
            inputs, out_root, cache = tmp / "inputs", tmp / "output", tmp / CACHE_PREFIX
            inputs.mkdir()
            progress("Copying inputs from storage", stage=False)
            sv.storage.download_tree(report_prefix(rid, "inputs"), inputs)
            # Earlier outputs come down too: review.json holds the reviewer's corrections.
            sv.storage.download_tree(report_prefix(rid, "output"), out_root)
            cached_before = sv.storage.download_tree(CACHE_PREFIX, cache)
            # The exact bytes this run reads, for the audit trail.
            input_hashes = {p.relative_to(inputs).as_posix(): _sha256(p)
                            for p in sorted(inputs.rglob("*")) if p.is_file()}

            claude = ProgressClaude(cache, sv.settings.llm_enabled, progress)
            facts, out_dir = pipeline.run(inputs, client, quarter, out_root, claude, progress)
            final_pdf, final_msg = None, None
            if kind == "finalize":
                progress("Finalizing: checking approval and rendering the final PDF")
                pdf, final_msg = pipeline.finalize(facts, inputs, out_dir)
                final_pdf = pdf.name if pdf else None
            if "narrative" in facts:
                _export_chart_images(facts, inputs, out_dir)

            # review.json belongs to the reviewer once it exists: the web app writes it, the worker never does.
            prefix = f"{report_prefix(rid, 'output')}/{out_dir.name}"
            had_review = sv.storage.exists(f"{prefix}/review.json")
            sv.storage.upload_tree(out_dir, prefix, skip={"review.json"} if had_review else set())
            # An approval covers one exact content. Once a draft says something else, the approval is
            # voided for good, so undoing the changes later cannot bring it back unnoticed.
            voided = None
            rv = review.load(out_dir)
            if rv["approved_hash"] and rv["approved_hash"] != review.content_hash(facts):
                voided = {"approved_hash": rv["approved_hash"], "approved_by": rv["reviewer"],
                          "approved_at": rv["approved_at"]}
                rv.update({"approved_hash": None, "approved_at": None, "voided": {**voided, "run": run_id}})
                sv.storage.write(f"{prefix}/review.json", json.dumps(rv, indent=2).encode("utf-8"))
            # New model answers go back to the shared cache, so the next run replays them for free.
            new_answers = sorted(set(claude.used) - {k.rsplit("/", 1)[1] for k in sv.storage.list(CACHE_PREFIX)})
            for name in new_answers:
                sv.storage.write(f"{CACHE_PREFIX}/{name}", (cache / name).read_bytes())

        issues = facts.get("issues", [])
        n_block = sum(i["level"] == "blocker" for i in issues)
        with sv.db.session() as s:
            run, report = s.get(Run, run_id), s.get(Report, rid)
            report.client_name = facts["client"]["name"]
            report.client_code = facts["client"]["code"]
            report.out_name = out_dir.name
            run.status = "failed" if kind == "finalize" and final_pdf is None else "succeeded"
            run.error = None if run.status == "succeeded" else f"Finalization refused: {final_msg}"
            run.n_blockers, run.n_warnings = n_block, len(issues) - n_block
            run.content_hash = review.content_hash(facts)
            run.final_pdf = final_pdf
            run.stage, run.progress, run.finished_at = "Done" if run.status == "succeeded" else "Refused", 100, now()
            s.commit()
        detail = {"blockers": n_block, "warnings": len(issues) - n_block,
                  "content_hash": review.content_hash(facts), "inputs_sha256": input_hashes,
                  "model_calls": facts.get("narrative", {}).get("llm", {}).get("calls"),
                  "cache_answers_available": cached_before, "new_cache_answers": len(new_answers)}
        if kind == "finalize":
            detail["finalize"] = final_msg
            detail["final_pdf"] = final_pdf
        sv.db.audit(rid, actor, f"{kind}_{'finished' if final_pdf or kind == 'draft' else 'refused'}", run_id, **detail)
        if voided:
            sv.db.audit(rid, actor, "approval_voided", run_id, content_hash=review.content_hash(facts),
                        reason="the new draft's content differs from the approved content", **voided)
        log.info("run finished", extra={"run_id": run_id, "report_id": rid, "kind": kind, "blockers": n_block})
    except Exception as e:  # a crash in one run must not stop the worker
        log.exception("run failed", extra={"run_id": run_id, "report_id": rid})
        with sv.db.session() as s:
            run = s.get(Run, run_id)
            run.status, run.stage, run.finished_at = "failed", "Failed", now()
            run.error = f"{type(e).__name__}: {e}"
            run.log = [*run.log, {"at": now().isoformat(timespec="seconds"), "message": traceback.format_exc()[-2000:]}]
            s.commit()
        sv.db.audit(rid, actor, f"{kind}_failed", run_id, error=f"{type(e).__name__}: {e}")


def fail_stale_runs(db: Database) -> None:
    with db.session() as s:
        s.execute(update(Run).where(Run.status == "running", Run.started_at < now() - STALE_AFTER)
                  .values(status="failed", stage="Failed", error="the worker stopped before finishing"))
        s.commit()


def drain(sv: Services) -> int:
    """Run every queued run now, in order. Used by tests and by a one-shot worker."""
    n = 0
    while (run := sv.db.claim_next_run()) is not None:
        execute(sv, run.id)
        n += 1
    return n


def work_forever(sv: Services, stop: threading.Event, poll_seconds: float = 1.0) -> None:
    stale_checked = False
    while not stop.is_set():
        # A dropped database connection must not end the loop: the thread would die quietly while the
        # web app kept serving, and queued runs would wait forever. Log it and retry on the next poll.
        try:
            if not stale_checked:
                fail_stale_runs(sv.db)
                stale_checked = True
            run = sv.db.claim_next_run()
            if run is not None:
                execute(sv, run.id)
                continue
        except Exception:
            log.exception("worker poll failed")
        stop.wait(poll_seconds)


def queued_or_running(db: Database, report_id: int) -> Run | None:
    with db.session() as s:
        return s.scalars(select(Run).where(Run.report_id == report_id, Run.status.in_(("queued", "running")))
                         .order_by(Run.id.desc())).first()


def main() -> None:
    from pmr_web import telemetry

    telemetry.configure()
    logging.basicConfig(level=logging.INFO)
    sv = Services.start(Settings.from_env())
    log.info("worker started")
    work_forever(sv, threading.Event(), poll_seconds=2.0)


if __name__ == "__main__":
    main()
