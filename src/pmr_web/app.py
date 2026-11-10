"""The web application: upload, process, review, approve and finalize a PMR in the browser.

The browser never computes or changes a figure. Every screen reads what the pipeline wrote
(facts.json, manifest.json) and every reviewer action writes review.json, the same file the command
line uses, then asks the worker for a new draft. Approval and finalization call the same functions
as `--approve` and `--finalize`, so the gate is enforced in one place.

Run locally:  flask --app pmr_web.app run   (or: python -m pmr_web)
"""

import copy
import hashlib
import json
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fasthtml.common import (A, Abbr, Button, Div, Form, H1, H2, H3, Iframe, Img, Input, Label, Li, Option, P, Pre,
                             Script, Select, Span, Table, Tbody, Td, Textarea, Th, Thead, Time, Tr, Ul)
from flask import Flask, Response, abort, g, redirect, request, session, url_for
from sqlalchemy import select
from werkzeug.routing import BaseConverter
from werkzeug.utils import secure_filename

from pmr import archive, discovery, narrative, review
from pmr.periods import Quarter
from pmr_web import evidence, telemetry
from pmr_web.config import Settings
from pmr_web.db import AuditEvent, InputFile, Report, Run, now
from pmr_web.storage import LocalStorage
from pmr_web.ui import alert, csrf_input, icon, kpi, page, pill, render, steps, tabs
from pmr_web.worker import Services, queued_or_running, report_prefix, work_forever

ALLOWED = {".xlsx", ".xlsm", ".pdf", ".pptx", ".docx", ".txt", ".csv", ".tsv", ".json", ".xml", ".zip"}

ATTESTATIONS = [
    ("charts", "I compared every chart value with its chart image and corrected any misread value."),
    ("narrative", "I read every printed narrative sentence beside its evidence."),
    ("issues", "I reviewed every warning and every figure marked missing."),
]


def create_app(settings: Settings | None = None, start_worker: bool | None = None) -> Flask:
    telemetry.configure()
    settings = settings or Settings.from_env()
    sv = Services.start(settings)
    app = Flask(__name__)
    app.url_map.converters["report"] = ReportRef
    app.secret_key = settings.secret_key
    app.config.update(MAX_CONTENT_LENGTH=200 * 1024 * 1024, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_HTTPONLY=True)
    app.extensions["pmr"] = sv
    if settings.embedded_worker if start_worker is None else start_worker:
        stop = threading.Event()
        worker = threading.Thread(target=work_forever, args=(sv, stop), daemon=True, name="pmr-worker")
        worker.start()
        app.extensions["pmr_worker"] = worker
        app.extensions["pmr_worker_stop"] = stop
    _routes(app, sv)
    return app


# ---------------------------------------------------------------- storage helpers

def _out_key(report: Report, name: str) -> str | None:
    return f"{report_prefix(report.id, 'output')}/{report.out_name}/{name}" if report.out_name else None


def _read_json(sv: Services, report: Report, name: str):
    key = _out_key(report, name)
    if key and sv.storage.exists(key):
        return json.loads(sv.storage.read(key))
    return None


def _review(sv: Services, report: Report) -> dict:
    data = _read_json(sv, report, "review.json")
    return {**copy.deepcopy(review.BLANK), **(data or {})}


def _save_review(sv: Services, report: Report, data: dict) -> None:
    sv.storage.write(_out_key(report, "review.json"), json.dumps(data, indent=2).encode("utf-8"))


def _local_inputs(sv: Services, report_id: int) -> Path:
    """A local folder holding the report's inputs, for the evidence viewer."""
    if isinstance(sv.storage, LocalStorage):
        return sv.storage.root / report_prefix(report_id, "inputs")
    folder = sv.settings.data_dir / "blobcache" / str(report_id)
    if not folder.is_dir():
        sv.storage.download_tree(report_prefix(report_id, "inputs"), folder)
    return folder


def _drop_local_inputs(sv: Services, report_id: int) -> None:
    if not isinstance(sv.storage, LocalStorage):
        shutil.rmtree(sv.settings.data_dir / "blobcache" / str(report_id), ignore_errors=True)


def _save_upload(sv: Services, report_id: int, rel: str, data: bytes, actor: str) -> None:
    sha = hashlib.sha256(data).hexdigest()
    sv.storage.write(f"{report_prefix(report_id, 'inputs')}/{rel}", data)
    with sv.db.session() as s:
        row = s.scalars(select(InputFile).where(InputFile.report_id == report_id, InputFile.path == rel)).first()
        if row is None:
            s.add(InputFile(report_id=report_id, path=rel, size=len(data), sha256=sha, uploaded_by=actor))
        else:
            row.size, row.sha256, row.uploaded_by = len(data), sha, actor
        s.commit()
    sv.db.audit(report_id, actor, "file_uploaded", path=rel, sha256=sha, size=len(data))


def _store(sv: Services, report_id: int, rel: str, data: bytes, actor: str) -> list[str]:
    """Saves one upload, unpacking a zip into its documents. Returns the names it skipped."""
    if Path(rel).suffix.lower() != ".zip":
        _save_upload(sv, report_id, rel, data, actor)
        return []
    skipped, saved = [], 0
    try:
        for path, contents in archive.unpack(data, rel, lambda m: _upload_name(m, False)):
            if contents is None:
                skipped.append(path)
                continue
            _save_upload(sv, report_id, path, contents, actor)
            saved += 1
    except zipfile.BadZipFile:
        return [rel]
    sv.db.audit(report_id, actor, "archive_unpacked", path=rel, sha256=hashlib.sha256(data).hexdigest(),
                files=saved, skipped=len(skipped))
    return skipped


def _preflight(docs: list[tuple[str, bytes]], client: str, quarter: str) -> str | None:
    """Why the uploaded documents cannot produce a report for this client and quarter, or None if they can.

    Runs the pipeline's own discovery on a scratch copy, so the answer matches what the run would find.
    Only a client that resolves to nothing, or a missing flash, refuses the create: those leave every
    figure empty. Other gaps (no deck, no allocation history) still draft and are shown as issues.
    """
    with tempfile.TemporaryDirectory(prefix="pmr_check_") as tmp:
        root = Path(tmp)
        for rel, data in docs:
            items = [(rel, data)]
            if Path(rel).suffix.lower() == ".zip":
                try:
                    items = list(archive.unpack(data, rel, lambda m: _upload_name(m, False)))
                except zipfile.BadZipFile:
                    items = []
            for path, contents in items:
                if contents is not None:
                    dest = root / path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(contents)
        sel = discovery.discover(root, client, Quarter.parse(quarter))
        if not {i.code for i in sel.issues} & {"client_unresolved", "flash_missing"}:
            return None
        found = discovery.inventory(root)
    if not found:
        return (f"None of these files is a filled-in flash report, so there are no figures for {client} {quarter}. "
                "Upload the drop folder that holds this quarter's flash workbook.")
    listed = "; ".join(f"{e['code']} ({e['name']}): {', '.join(e['quarters'])}" if e["code"]
                       else f"{e['name']}: {', '.join(e['quarters'])}" for e in found)
    return (f"No flash report in these files is for {client} {quarter}, so no report was created. "
            f"The files have flash reports for {listed}. Pick one of those, or upload the files for "
            f"{client} {quarter}.")


def _upload_name(raw: str, from_folder: bool) -> str | None:
    """A safe relative path for an uploaded file, or None if the file type is not a source document."""
    parts = [secure_filename(p) for p in raw.replace("\\", "/").split("/") if p not in ("", ".", "..")]
    # A folder upload names "<folder>/<sub>/<file>"; the chosen folder itself is not part of the layout.
    if from_folder and len(parts) > 1:
        parts = parts[1:]
    parts = [p for p in parts if p]
    if not parts or parts[-1].startswith(".") or Path(parts[-1]).suffix.lower() not in ALLOWED:
        return None
    return "/".join(parts)



def _drop_zone(folder_label: str, files_label: str):
    """The folder and file pickers inside a box that also takes drag and drop (the drop script is in ui.JS)."""
    return Div(Div(icon("upload"), "Drag and drop files or folders here, or choose them below", cls="hint"),
               Div(Div(Label(folder_label),
                       Input(type="file", name="folder", webkitdirectory=True, multiple=True), cls="field"),
                   Div(Label(files_label), Input(type="file", name="files", multiple=True,
                                                 accept=",".join(sorted(ALLOWED))), cls="field"),
                   cls="grid gap-4", style="grid-template-columns:1fr 1fr"),
               P(Span(data_drop_count=True), " ",
                 Button("Clear", type="button", cls="btn ghost", onclick="dropClear(this)"),
                 cls="small", data_drop_status=True, hidden=True, style="margin-top:10px"),
               cls="drop", data_drop=True)


class ReportRef(BaseConverter):
    """A report in a URL: its readable slug ending in the id, e.g. cpers-4q25-draft-2026-11-08-2148-est-6, or the
    bare id. Only the trailing id is read, so links made before a later draft changed the slug still work."""

    regex = r"(?:[A-Za-z0-9-]*-)?\d+"

    def to_python(self, value: str) -> int:
        return int(value.rsplit("-", 1)[-1])

    def to_url(self, value) -> str:
        return str(value)


def _report_path(report: Report) -> str:
    """The report's URL: client, quarter and the last draft's build time in US Eastern time (the creation time before
    any draft is built), then the id. The server cannot know the viewer's zone, so the URL always uses Eastern."""
    draft = _last_draft(report) if _built(report) else None
    at = (draft.finished_at if draft else None) or report.created_at
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    et = at.astimezone(EASTERN)
    parts = [report.client_code or report.client, report.quarter, "draft" if draft else "created",
             f"{et:%Y-%m-%d-%H%M-%Z}", str(report.id)]
    return "/reports/" + "-".join(x for x in (re.sub(r"[^a-z0-9]+", "-", p.lower()).strip("-") for p in parts) if x)

# ---------------------------------------------------------------- report state

def _runs(report: Report) -> list[Run]:
    return list(report.runs)  # newest first


def _built(report: Report) -> Run | None:
    """The run whose outputs are in storage now: the newest one that got as far as writing them."""
    return next((r for r in _runs(report) if r.content_hash), None)


def _last_draft(report: Report) -> Run | None:
    """The newest run that built a draft. A finalize run rebuilds too, but it is not a new draft."""
    return next((r for r in _runs(report) if r.content_hash and r.kind == "draft"), None) or _built(report)


def _pending(report: Report, built: Run | None) -> bool:
    """Reviewer changes are saved but the draft on screen does not include them yet."""
    return built is not None and report.review_rev > built.review_rev


def _approval_holds(report: Report, rv: dict, built: Run | None) -> bool:
    """The approval covers the draft on screen, and no saved reviewer change is waiting to alter it."""
    return built is not None and rv.get("approved_hash") == built.content_hash and not _pending(report, built)


def _state(report: Report, rv: dict) -> tuple[str, str, int]:
    """(label, tone, step) for the report's current position in the workflow."""
    runs = _runs(report)
    built = _built(report)
    if runs and runs[0].status in ("queued", "running"):
        return "Processing", "info", 1
    if built is None:
        return ("Failed", "bad", 1) if runs else ("No draft", "info", 0)
    if not _pending(report, built) and any(r.kind == "finalize" and r.final_pdf and r.content_hash == built.content_hash
                                            for r in runs):
        return "Final", "final", 6
    if _approval_holds(report, rv, built):
        return "Approved", "accent", 5
    if built.n_blockers:
        return "Blocked", "bad", 2
    return "In review", "warn", 3


def _queue(sv: Services, report: Report, kind: str, actor: str) -> tuple[bool, str]:
    if queued_or_running(sv.db, report.id):
        return False, "A run is already queued or in progress for this report."
    with sv.db.session() as s:
        rep = s.get(Report, report.id)
        s.add(Run(report_id=rep.id, kind=kind, requested_by=actor, review_rev=rep.review_rev, log=[]))
        s.commit()
    sv.db.audit(report.id, actor, f"{kind}_queued")
    return True, "Draft queued." if kind == "draft" else "Finalization queued."


# ---------------------------------------------------------------- routes

def _routes(app: Flask, sv: Services) -> None:
    def user() -> str | None:
        if sv.settings.trust_easy_auth:
            # App Service authentication (Microsoft Entra ID) signs the user in before the request arrives.
            name = request.headers.get("X-MS-CLIENT-PRINCIPAL-NAME")
            if name:
                return name
        return session.get("user")

    def csrf() -> str:
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(24)
        return session["csrf"]

    def say(message: str, tone: str = "") -> None:
        session.setdefault("flash", []).append([message, tone])
        session.modified = True

    def flashes():
        return [alert(m, tone=t) for m, t in session.pop("flash", [])]

    def html(title: str, *content) -> str:
        return page(title, *flashes(), *content, user=g.user or "", csrf=csrf())

    @app.before_request
    def gate():
        g.user = user()
        if request.endpoint in ("signin", "health", "version", "static"):
            return None
        if not g.user:
            return redirect(url_for("signin", next=request.path))
        if request.method == "POST":
            token = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
            if not token or token != session.get("csrf"):
                abort(400, "missing or invalid CSRF token")
        return None

    def report_or_404(report_id: int) -> Report:
        s = sv.db.session()
        report = s.get(Report, report_id)
        if report is None or report.deleted_at is not None:
            abort(404)
        g.db_session = s  # kept open so lazy relationships load during rendering
        return report

    @app.teardown_request
    def close_session(_exc):
        s = g.pop("db_session", None)
        if s is not None:
            s.close()

    @app.get("/health")
    def health():
        # App Service restarts an instance that keeps failing this, which also restarts a dead worker.
        worker = app.extensions.get("pmr_worker")
        if worker is not None and not worker.is_alive():
            return {"ok": False, "worker": "stopped"}, 503
        return {"ok": True}

    @app.get("/version")
    def version():
        """The commit the image was built from, so a deploy can wait until the new container serves."""
        return {"version": os.environ.get("PMR_VERSION", "dev")}

    @app.route("/signin", methods=["GET", "POST"])
    def signin():
        """Name sign-in, locally and in the slim Azure deployment, where a shared access code guards it.
        With App Service authentication on, Entra ID signs users in instead."""
        if sv.settings.trust_easy_auth:
            return redirect("/")
        code = sv.settings.access_code
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            given = request.form.get("code", "").strip()
            if code and not secrets.compare_digest(given.encode(), code.encode()):
                say("That access code is not right.", "bad")
            elif name:
                session["user"] = name
                return redirect(request.args.get("next") or "/")
        g.user = None
        code_field = (Div(Label("Access code", fr="code"), Input(type="password", name="code", id="code",
                                                                  required=True, autocomplete="off"), cls="field")
                      if code else "")
        form = Form(Div(Label("Your name", fr="name"), Input(type="text", name="name", id="name", required=True,
                                                             autofocus=True), cls="field"),
                    code_field,
                    Input(type="hidden", name="csrf", value=csrf()),
                    Button("Continue", cls="btn primary lg", style="margin-top:12px"), method="post")
        note = ("Your name is recorded on every action and on the approval. The access code came with the "
                "invitation to this demo." if code else
                "Your name is recorded on every action and on the approval. In Azure this "
                "comes from your Microsoft Entra ID sign-in instead.")
        return html("Sign in", Div(P("Demo sign-in" if code else "Local sign-in", cls="eyebrow"),
                                   H1("Who is reviewing?"), P(note, cls="muted"),
                                   form, cls="panel", style="max-width:460px;margin:40px auto"))

    @app.post("/signout")
    def signout():
        session.clear()
        if sv.settings.trust_easy_auth:
            return redirect("/.auth/logout")
        return redirect(url_for("signin"))

    @app.get("/")
    def dashboard():
        archived = request.args.get("archived") == "1"
        with sv.db.session() as s:
            live = select(Report).where(Report.deleted_at.is_(None))
            reports = s.scalars(live.where(Report.archived_at.is_not(None) if archived else Report.archived_at.is_(None))
                                .order_by(Report.id.desc())).all()
            n_other = len(s.scalars(live.where(Report.archived_at.is_(None) if archived
                                               else Report.archived_at.is_not(None))).all())
            rows = []
            for r in reports:
                label, tone, _ = _state(r, _review(sv, r))
                built = _built(r)
                blockers = built.n_blockers if built else None
                rows.append(Tr(
                    Td(A(r.client_name or r.client, href=_report_path(r), cls="client"),
                       Div(r.client_code or "", cls="small muted mono")),
                    Td(r.quarter, cls="num"), Td(pill(label, tone)),
                    Td("" if blockers is None else str(blockers), cls="r num",
                       style="color:var(--bad);font-weight:600" if blockers else ""),
                    Td(str(built.n_warnings) if built else "", cls="r num"),
                    Td(_when(_last_draft(r).finished_at) if built else "", cls="small muted",
                       style="padding-left:40px"),
                    Td(Div(*_report_actions(r, csrf(), archived),
                           A(icon("chevron", 18), href=_report_path(r), cls="go", aria_label="Open report"),
                           cls="row-actions"), cls="r")))
        empty = ("No archived reports." if archived
                 else "No reports yet. Start one from a drop folder of source documents.")
        table = (Table(Thead(Tr(Th("Client"), Th("Quarter"), Th("Status"), Th("Blockers"), Th("Warnings"),
                                 Th("Last draft"), Th(""))), Tbody(*rows), cls="t")
                 if rows else P(empty, cls="muted"))
        switch = (A("Back to active reports", href="/", cls="link small")
                  if archived else A(f"Archived reports ({n_other})", href="/?archived=1", cls="link small")
                  if n_other else "")
        return html("Archived reports" if archived else "Reports",
                    Div(Div(P("Quarterly performance measurement", cls="eyebrow"),
                            H1("Archived reports" if archived else "Reports", style="font-size:40px;margin-top:8px")),
                        Div(switch, *([] if archived else [A(icon("plus"), "New report", href="/reports/new",
                                                             cls="btn primary lg")]),
                            cls="flex items-center gap-6"),
                        cls="flex items-end justify-between", style="padding-top:12px;margin-bottom:28px"),
                    *([P("Archived reports keep their files, drafts, approvals and audit trail. Restore one to work "
                         "on it again. Deleting removes its files, drafts and runs for good and keeps only the "
                         "audit trail.", cls="muted", style="margin-bottom:20px;max-width:720px")] if archived else []),
                    Div(table, cls="reveal"))

    @app.post("/reports/<report:report_id>/archive")
    def archive_report(report_id: int):
        report = report_or_404(report_id)
        if queued_or_running(sv.db, report.id):
            say("Wait for the current run to finish before archiving this report.", "warn")
            return redirect("/")
        report.archived_at = now()
        g.db_session.commit()
        sv.db.audit(report.id, g.user, "report_archived")
        say(f"Archived {report.client_name or report.client} {report.quarter}. Find it under Archived reports.", "ok")
        return redirect("/")

    @app.post("/reports/<report:report_id>/restore")
    def restore_report(report_id: int):
        report = report_or_404(report_id)
        report.archived_at = None
        g.db_session.commit()
        sv.db.audit(report.id, g.user, "report_restored")
        say(f"Restored {report.client_name or report.client} {report.quarter}.", "ok")
        return redirect("/?archived=1")

    @app.post("/reports/<report:report_id>/delete")
    def delete_report(report_id: int):
        report = report_or_404(report_id)
        # Two steps, so one stray click cannot destroy a report: archive first, then delete from the archive.
        if report.archived_at is None:
            say("Archive the report before deleting it.", "warn")
            return redirect("/")
        if queued_or_running(sv.db, report.id):
            say("Wait for the current run to finish before deleting this report.", "warn")
            return redirect("/?archived=1")
        name = f"{report.client_name or report.client} {report.quarter}"
        keys = sv.storage.list(f"reports/{report.id}")
        for key in keys:
            sv.storage.delete(key)
        if isinstance(sv.storage, LocalStorage):
            sv.storage.delete_tree(f"reports/{report.id}")
        _drop_local_inputs(sv, report.id)
        s = g.db_session
        for row in [*report.runs, *report.files]:
            s.delete(row)
        report.deleted_at = now()
        s.commit()
        # The audit trail stays: rows are only ever added, and this one records what was removed.
        sv.db.audit(report.id, g.user, "report_deleted", files_removed=len(keys))
        say(f"Deleted {name}. Its audit trail is kept.", "ok")
        return redirect("/?archived=1")

    @app.get("/reports/new")
    def new_report():
        with sv.db.session() as s:
            known = sorted({c for r in s.scalars(select(Report)).all() for c in (r.client_code, r.client) if c})
        quarters = _quarter_choices()
        # After a refused create, keep what the preparer typed.
        chosen = request.args.get("quarter", "")
        if chosen and chosen not in quarters:
            quarters.append(chosen)
        sample = sv.settings.sample_inputs
        form = Form(
            csrf_input(csrf()),
            Div(Div(Label("Client", fr="client"),
                    Input(type="text", name="client", id="client", list="clients", required=True,
                          value=request.args.get("client", ""),
                          placeholder="Short code or full name, as in the prior PMR"),
                    _datalist("clients", known),
                    P("Resolved against the prior PMR, so either the code or the full name works.",
                      cls="small muted"), cls="field"),
                Div(Label("Quarter", fr="quarter"),
                    Select(*[Option(q, value=q, selected=(q == chosen if chosen else i == 1))
                             for i, q in enumerate(quarters)],
                           name="quarter", id="quarter", cls="inp"),
                    P("The reporting quarter. Files for other quarters are skipped with a reason.",
                      cls="small muted"), cls="field"),
                cls="grid gap-4", style="grid-template-columns:2fr 1fr"),
            H3("Source documents", style="margin-top:20px"),
            P("Upload the whole drop folder: flash workbooks, prior PMRs, IC logs, allocation history, manager "
              "reports and the outlook deck. Files are classified by content, not by name, and anything that "
              "does not belong to this client and quarter is skipped and listed.", cls="muted"),
            _drop_zone("Choose a folder", "Or choose files"),
            *([Label(Input(type="checkbox", name="sample", value="1"),
                     f" Start from the sample drop folder ({sample.name}/), for a demonstration",
                     style="display:block;margin-top:14px")] if sample else []),
            Div(Button("Create report and run the first draft", cls="btn primary lg"), style="margin-top:20px"),
            method="post", action="/reports", enctype="multipart/form-data")
        return html("New report", P("New report", cls="eyebrow"), H1("Start a quarterly report"),
                    Div(form, cls="panel reveal", style="margin-top:16px;max-width:860px"))

    @app.post("/reports")
    def create_report():
        client = request.form.get("client", "").strip()
        try:
            quarter = Quarter.parse(request.form.get("quarter", "")).code
        except ValueError:
            say("The quarter is not valid; write it like 1Q26.", "bad")
            return redirect("/reports/new")
        if not client:
            say("Enter a client code or name.", "bad")
            return redirect("/reports/new")
        uploads = [(f, True) for f in request.files.getlist("folder")] + \
                  [(f, False) for f in request.files.getlist("files")]
        use_sample = request.form.get("sample") == "1" and sv.settings.sample_inputs
        if not use_sample and not any(f.filename for f, _ in uploads):
            say("Upload the source documents, or start from the sample drop folder.", "bad")
            return redirect("/reports/new")
        docs, skipped = [], []
        if use_sample:
            root = sv.settings.sample_inputs
            for p in sorted(root.rglob("*")):
                if p.is_file() and p.suffix.lower() in ALLOWED and not p.name.startswith((".", "~$")):
                    docs.append((p.relative_to(root).as_posix(), p.read_bytes()))
        for f, from_folder in uploads:
            if not f.filename:
                continue
            rel = _upload_name(f.filename, from_folder)
            if rel is None:
                skipped.append(f.filename)
                continue
            docs.append((rel, f.read()))
        # Check the client and quarter against the files before anything is created, so a typo does not
        # leave an empty report behind a blocker.
        problem = _preflight(docs, client, quarter)
        if problem:
            say(problem, "bad")
            return redirect(url_for("new_report", client=client, quarter=quarter))
        with sv.db.session() as s:
            report = Report(client=client, quarter=quarter, created_by=g.user)
            s.add(report)
            s.commit()
            rid = report.id
        sv.db.audit(rid, g.user, "report_created", client=client, quarter=quarter)
        for rel, data in docs:
            skipped += _store(sv, rid, rel, data, g.user)
        if skipped:
            say(f"Ignored {len(skipped)} file(s) with an unsupported type, or archives that could not be unpacked: "
                f"{', '.join(skipped[:5])}", "warn")
        with sv.db.session() as s:
            report = s.get(Report, rid)
            _queue(sv, report, "draft", g.user)
            path = _report_path(report)
        return redirect(path)

    @app.get("/reports/<report:report_id>")
    def report_page(report_id: int):
        report = report_or_404(report_id)
        tab = request.args.get("tab", "overview")
        rv = _review(sv, report)
        facts = _read_json(sv, report, "facts.json") if _built(report) else None
        manifest = _read_json(sv, report, "manifest.json") if facts else None
        label, _, step = _state(report, rv)
        active = queued_or_running(sv.db, report.id)
        built = _built(report)
        n = facts.get("narrative", {}) if facts else {}
        n_sent = sum(len(_sentences(b)) for b in _blocks(n))
        n_chart = sum(len(sr["points"]) for ch in n.get("charts", []) for sr in ch["series"])
        n_fig = len([e for e in manifest or [] if e["kind"] in ("figure", "quote", "missing")])
        base = _report_path(report)
        items = [("overview", "Overview", None), ("figures", "Figures", n_fig or None),
                 ("narrative", "Narrative", n_sent or None), ("charts", "Chart values", n_chart or None),
                 ("pdf", "Draft PDF", None), ("approval", "Approval", None),
                 ("inputs", "Inputs", len(report.files)), ("audit", "Audit trail", None)]
        title = facts["client"]["name"] if facts and facts["client"].get("name") else report.client
        quarter = facts["quarter"]["label"] if facts else report.quarter
        subtitle = (quarter, ". Last draft built ", _when(_last_draft(report).finished_at), ".") if built else \
            (quarter, ".")
        buttons = [*([A(icon("file"), "Draft PDF", href=f"{base}?tab=pdf", cls="btn lg")] if built else []),
                   *([] if active else [_post_button(f"{base}/runs", "Regenerate draft", csrf(), "primary lg",
                                                     mark="refresh", confirm=_regenerate_warning(report, rv))])]
        header = Div(
            Div(Div(Div(A("Reports", href="/"), icon("chevron", 12),
                        Span(f"{report.client_code or report.client} / {report.quarter}", cls="mono small"),
                        cls="crumbs"),
                    H1(title, style="margin-top:8px"),
                    P(*subtitle, cls="muted", style="font-size:15px;margin-top:8px")),
                Div(*buttons, cls="flex items-center gap-3"), cls="flex items-end justify-between gap-6"),
            steps(step), cls="flex flex-col", title=f"Status: {label}")
        pending = (built is not None and report.review_rev > built.review_rev and not active)
        banner = []
        if active:
            banner.append(_status_card(active, poll=True))
        elif pending:
            n_changes = report.review_rev - built.review_rev
            banner.append(alert(f"You have {n_changes} saved change{'s' if n_changes != 1 else ''} that "
                                f"{'are' if n_changes != 1 else 'is'} not in this draft yet."))
        last = _runs(report)[0] if _runs(report) else None
        if last and last.status == "failed" and not active:
            banner.append(alert(Span(f"The last {last.kind} run did not finish: ", cls="font-semibold"),
                                Span(last.error or "unknown error"), tone="bad"))
        ctx = dict(sv=sv, report=report, facts=facts, manifest=manifest, rv=rv, csrf=csrf(), base=base,
                   built=built, active=active, pending=pending)
        body = {"overview": _overview, "figures": _figures, "narrative": _narrative, "charts": _charts,
                "pdf": _pdf, "approval": _approval, "inputs": _inputs, "audit": _audit}.get(tab, _overview)(**ctx)
        if report.archived_at is not None:
            banner.insert(0, alert(Div(Span("This report was archived on ", _when(report.archived_at),
                                            ". It is hidden from the report list."),
                                       _post_button(f"{base}/restore", "Restore", csrf(), "", mark="undo"),
                                       cls="flex items-center justify-between gap-4")))
        # Show the readable URL for the current draft, whatever URL opened the page (an id, or an older draft's).
        here = Script(f"if(location.pathname!=={json.dumps(base)})"
                      f"history.replaceState(null,'',{json.dumps(base)}+location.search+location.hash)")
        return html(title, header, tabs(base, tab, items), *banner, body, here)

    @app.get("/reports/<report:report_id>/status")
    def run_status(report_id: int):
        report = report_or_404(report_id)
        run = _runs(report)[0] if _runs(report) else None
        if run is None:
            return ""
        if run.status not in ("queued", "running"):
            # The run finished: reload the page so every tab shows the new draft.
            return Response("", headers={"HX-Refresh": "true"})
        return render(_status_card(run, poll=True))

    @app.post("/reports/<report:report_id>/runs")
    def start_run(report_id: int):
        report = report_or_404(report_id)
        ok, msg = _queue(sv, report, "draft", g.user)
        say(msg, "" if ok else "warn")
        return redirect(request.referrer or f"/reports/{report_id}")

    @app.post("/reports/<report:report_id>/files")
    def upload_files(report_id: int):
        report = report_or_404(report_id)
        if queued_or_running(sv.db, report.id):
            say("Wait for the current run to finish before changing inputs.", "warn")
            return redirect(f"/reports/{report_id}?tab=inputs")
        n, skipped = 0, []
        for field, from_folder in (("folder", True), ("files", False)):
            for f in request.files.getlist(field):
                rel = _upload_name(f.filename or "", from_folder)
                if rel:
                    missed = _store(sv, report.id, rel, f.read(), g.user)
                    skipped += missed
                    # A zip that could not be unpacked saved nothing.
                    n += missed != [rel]
        _drop_local_inputs(sv, report.id)
        say(f"Uploaded {n} file(s). Regenerate the draft to use them." if n else "No source documents uploaded.",
            "ok" if n else "warn")
        if skipped:
            say(f"Ignored {len(skipped)} file(s) with an unsupported type, or archives that could not be unpacked: "
                f"{', '.join(skipped[:5])}", "warn")
        return redirect(f"/reports/{report_id}?tab=inputs")

    @app.post("/reports/<report:report_id>/files/delete")
    def delete_file(report_id: int):
        report = report_or_404(report_id)
        path = request.form.get("path", "")
        if queued_or_running(sv.db, report.id):
            say("Wait for the current run to finish before changing inputs.", "warn")
            return redirect(f"/reports/{report_id}?tab=inputs")
        s = g.db_session
        row = s.scalars(select(InputFile).where(InputFile.report_id == report.id, InputFile.path == path)).first()
        if row:
            sv.storage.delete(f"{report_prefix(report.id, 'inputs')}/{path}")
            s.delete(row)
            s.commit()
            _drop_local_inputs(sv, report.id)
            sv.db.audit(report.id, g.user, "file_removed", path=path, sha256=row.sha256)
            say(f"Removed {path}. Regenerate the draft to apply.", "ok")
        else:
            say(f"Nothing removed: {path or 'no file'} is not one of this report's uploaded files.", "bad")
        return redirect(f"/reports/{report_id}?tab=inputs")

    def reviewer_change(report: Report, action: str, **detail) -> None:
        s = g.db_session
        report.review_rev += 1
        s.commit()
        sv.db.audit(report.id, g.user, action, **detail)

    def editable(report: Report) -> str | None:
        """Why the review file cannot change now, or None if it can."""
        if queued_or_running(sv.db, report.id):
            return "Wait for the current run to finish."
        if not report.out_name:
            return "There is no draft to review yet."
        return None

    @app.post("/reports/<report:report_id>/sentences")
    def sentence_action(report_id: int):
        report = report_or_404(report_id)
        back = f"/reports/{report_id}?tab=narrative"
        if why := editable(report):
            say(why, "warn")
            return redirect(back)
        action, drafted = request.form.get("action"), request.form.get("drafted", "")
        rv = _review(sv, report)
        facts = _read_json(sv, report, "facts.json") or {}
        if action == "exclude":
            if drafted not in rv["excluded_sentences"]:
                rv["excluded_sentences"].append(drafted)
            msg = "Sentence excluded."
        elif action == "restore":
            rv["excluded_sentences"] = [t for t in rv["excluded_sentences"] if t != drafted]
            msg = "Sentence restored."
        elif action == "revert":
            rv["edited_sentences"].pop(drafted, None)
            msg = "Edit reverted to the drafted wording."
        elif action == "edit":
            new = " ".join(request.form.get("text", "").split())
            sentence = _find_sentence(facts, drafted)
            if sentence is None:
                say("That sentence is not in the current draft.", "bad")
                return redirect(back)
            # The same check the pipeline applies, run now so the reviewer sees the reason at once.
            if problems := narrative.edit_problems(sentence, new):
                say("Edit not saved: " + "; ".join(problems) + ". An edit may reword a sentence but not add a "
                    "number or name its evidence does not contain.", "bad")
                return redirect(back)
            if new == drafted:
                rv["edited_sentences"].pop(drafted, None)
            else:
                rv["edited_sentences"][drafted] = new
            msg = "Edit saved."
        else:
            abort(400)
        _save_review(sv, report, rv)
        reviewer_change(report, f"sentence_{action}", drafted=drafted,
                        new=rv["edited_sentences"].get(drafted) if action == "edit" else None)
        say(msg + " Regenerate the draft to apply it.", "ok")
        return redirect(back)

    @app.post("/reports/<report:report_id>/charts")
    def chart_correction(report_id: int):
        report = report_or_404(report_id)
        back = f"/reports/{report_id}?tab=charts"
        if why := editable(report):
            say(why, "warn")
            return redirect(back)
        point = request.form.get("point", "")
        facts = _read_json(sv, report, "facts.json") or {}
        charts = {p["id"]: ch for ch in facts.get("narrative", {}).get("charts", [])
                  for sr in ch["series"] for p in sr["points"]}
        points = {p["id"]: p for ch in facts.get("narrative", {}).get("charts", [])
                  for sr in ch["series"] for p in sr["points"]}
        if point not in points:
            abort(400)
        rv = _review(sv, report)
        if request.form.get("clear"):
            rv["chart_value_corrections"].pop(point, None)
            detail = {"point": point, "value": None}
        else:
            try:
                value = float(request.form.get("value", "").replace(",", ""))
            except ValueError:
                value = None
            lo, hi = _correction_range(charts[point])
            # float() accepts "nan", "inf" and "1e400"; none of them is a value a chart can show.
            if value is None or not math.isfinite(value) or not lo <= value <= hi:
                say(f"Enter a number between {lo:g} and {hi:g}. The chart's axis runs from "
                    f"{charts[point]['axis'][0]:g} to {charts[point]['axis'][1]:g}.", "bad")
                return redirect(back)
            model_value = points[point].get("model_value", points[point]["value"])
            rv["chart_value_corrections"][point] = value
            detail = {"point": point, "value": value, "model_value": model_value}
        _save_review(sv, report, rv)
        reviewer_change(report, "chart_value_corrected", **detail)
        say("Chart value saved. Regenerate the draft to apply it. The market update is drafted from the chart "
            "values, so the regeneration asks the model again unless this exact value was used before.", "ok")
        return redirect(back)

    def facts_dir(report: Report):
        """A temporary folder with review.json, so approval runs through the pipeline's own functions."""
        tmp = Path(tempfile.mkdtemp(prefix="pmr_review_"))
        (tmp / "review.json").write_text(json.dumps(_review(sv, report)), encoding="utf-8")
        return tmp

    @app.post("/reports/<report:report_id>/approve")
    def approve(report_id: int):
        report = report_or_404(report_id)
        back = f"/reports/{report_id}?tab=approval"
        built = _built(report)
        facts = _read_json(sv, report, "facts.json")
        problems = _approval_problems(report, built, facts, queued_or_running(sv.db, report.id))
        missing = [k for k, _ in ATTESTATIONS if request.form.get(f"attest_{k}") != "1"]
        if missing:
            problems.append("confirm each review statement")
        if problems:
            say("Approval refused: " + "; ".join(problems) + ".", "bad")
            return redirect(back)
        tmp = facts_dir(report)
        try:
            ok, msg = review.approve(tmp, facts, g.user)
            if ok:
                _save_review(sv, report, json.loads((tmp / "review.json").read_text(encoding="utf-8")))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        sv.db.audit(report.id, g.user, "approved" if ok else "approval_refused", built.id,
                    **({} if ok else {"message": msg}), content_hash=review.content_hash(facts),
                    attested=[k for k, _ in ATTESTATIONS])
        say(("Approved. " if ok else "Approval refused: ") + msg, "ok" if ok else "bad")
        return redirect(back)

    @app.post("/reports/<report:report_id>/unapprove")
    def unapprove(report_id: int):
        report = report_or_404(report_id)
        rv = _review(sv, report)
        if rv["approved_hash"]:
            old = rv["approved_hash"]
            rv.update({"approved_hash": None, "approved_at": None})
            _save_review(sv, report, rv)
            sv.db.audit(report.id, g.user, "approval_withdrawn", content_hash=old)
            say("Approval withdrawn.", "ok")
        return redirect(f"/reports/{report_id}?tab=approval")

    @app.post("/reports/<report:report_id>/finalize")
    def finalize(report_id: int):
        report = report_or_404(report_id)
        facts = _read_json(sv, report, "facts.json")
        if facts is None:
            abort(400)
        tmp = facts_dir(report)
        try:
            ok, why = review.can_finalize(tmp, facts)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if ok and report.review_rev > (_built(report).review_rev if _built(report) else 0):
            ok, why = False, "reviewer changes are not yet in the draft"
        if not ok:
            say(f"Finalization refused: {why}.", "bad")
            return redirect(f"/reports/{report_id}?tab=approval")
        # The worker rebuilds the draft from the stored inputs and finalizes only if it still matches.
        queued, msg = _queue(sv, report, "finalize", g.user)
        say(msg, "ok" if queued else "warn")
        return redirect(f"/reports/{report_id}?tab=approval")

    @app.get("/reports/<report:report_id>/evidence")
    def evidence_view(report_id: int):
        report = report_or_404(report_id)
        loc = request.args.get("loc", "")
        quotes = request.args.getlist("q")
        label = request.args.get("label", "")
        view = evidence.view(_local_inputs(sv, report.id), loc, quotes)
        kind = {".pdf": "PDF", ".pptx": "Slides", ".xlsx": "Workbook", ".xlsm": "Workbook"}.get(
            Path(loc.split("#", 1)[0]).suffix.lower(), "")
        shown = [q for q in quotes if q and not q.replace(".", "").replace(",", "").replace("$", "")
                 .replace("%", "").strip().lstrip("-").isdigit()]
        return render(Div(Div(P("Source evidence", cls="eyebrow"), Span(kind, cls="mono small muted"), cls="ev-head"),
                          *([H3(label, style="margin-top:6px")] if label else []),
                          Div(loc, cls="mono small", style="word-break:break-all;margin-top:10px;color:var(--ink)"),
                          *([Div(P("Matched quote" if len(shown) == 1 else "Matched quotes", cls="lbl"),
                                 *[P(q) for q in shown], cls="ev-quote")] if shown else []),
                          view, cls="reveal"))

    @app.get("/reports/<report:report_id>/out/<path:name>")
    def output_file(report_id: int, name: str):
        report = report_or_404(report_id)
        key = _out_key(report, name)
        if key is None or ".." in name or not sv.storage.exists(key):
            abort(404)
        types = {".pdf": "application/pdf", ".json": "application/json", ".png": "image/png",
                 ".jpg": "image/jpeg", ".gif": "image/gif", ".html": "text/html"}
        mime = types.get(Path(name).suffix.lower(), "application/octet-stream")
        inline = mime.startswith("image/") or (mime == "application/pdf" and not request.args.get("download"))
        disp = f"{'inline' if inline else 'attachment'}; filename=\"{Path(name).name}\""
        resp = Response(sv.storage.read(key), mimetype=mime, headers={"Content-Disposition": disp})
        if Path(name).suffix.lower() == ".html":
            # The static review page embeds images as data URIs and needs no scripts.
            resp.headers["Content-Security-Policy"] = "default-src 'none'; img-src data:; style-src 'unsafe-inline'"
        return resp


# ---------------------------------------------------------------- tab bodies

EASTERN = ZoneInfo("America/New_York")


def _when(at, style: str = "when"):
    """A stored UTC time as shown in the app: "when" gives 2 Nov 2026, 09:08 EST, "stamp" 2026-11-02 09:08:05 EST
    and "clock" 09:08:05 EST. The server writes US Eastern time; the browser rewrites it in the viewer's own zone
    (localTimes in ui.JS) and keeps Eastern when it cannot tell what that zone is."""
    if not at:
        return ""
    at = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    et = at.astimezone(EASTERN)
    text = {"when": f"{et.day} {et:%b %Y, %H:%M %Z}", "stamp": f"{et:%Y-%m-%d %H:%M:%S %Z}",
            "clock": f"{et:%H:%M:%S %Z}"}[style]
    return Time(text, datetime=at.astimezone(UTC).isoformat(timespec="seconds"), data_tz=style)


def _quarter_choices() -> list[str]:
    """The current quarter first after the next one, then the seven before it; reporting usually lags a quarter."""
    from datetime import date

    today = date.today()
    q = Quarter(today.year, (today.month - 1) // 3 + 1)
    out = [q.next(), q]
    for _ in range(7):
        out.append(out[-1].previous())
    return [x.code for x in out]


def _datalist(id_: str, values: list[str]):
    from fasthtml.common import Datalist

    return Datalist(*[Option(value=v) for v in values], id=id_)


def _post_button(url: str, label: str, csrf: str, tone: str = "", mark: str = "", confirm: str = "", **fields):
    return Form(csrf_input(csrf), *[Input(type="hidden", name=k, value=v) for k, v in fields.items()],
                Button(*([icon(mark)] if mark else []), label, cls=f"btn {tone}"), method="post", action=url,
                style="display:inline", **({"onsubmit": f"return confirm({json.dumps(confirm)})"} if confirm else {}))


def _report_actions(report: Report, csrf: str, archived: bool) -> list:
    """Archive on the active list; Restore and Delete on the archived list."""
    name = f"{report.client_name or report.client} {report.quarter}"
    if not archived:
        return [_post_button(f"/reports/{report.id}/archive", "Archive", csrf, "ghost", mark="archive")]
    return [_post_button(f"/reports/{report.id}/restore", "Restore", csrf, "ghost", mark="undo"),
            _post_button(f"/reports/{report.id}/delete", "Delete", csrf, "ghost danger", mark="trash",
                         confirm=f"Delete {name} for good? Its uploaded files, drafts, PDFs and run history are "
                                 "removed and cannot be recovered. The audit trail is kept.")]


def _regenerate_warning(report: Report, rv: dict) -> str:
    """A confirmation to show before regenerating over an approval or a final PDF; empty when none is needed."""
    if any(r.kind == "finalize" and r.final_pdf for r in report.runs):
        return ("A final PDF exists. Regenerate the draft anyway? If the new draft differs, the approval is voided "
                "and the final PDF is marked as superseded.")
    if rv.get("approved_hash"):
        return "This report is approved. Regenerate the draft anyway? If the new draft differs, the approval is voided."
    return ""


def _correction_range(chart: dict) -> tuple[float, float]:
    """The values a chart correction may take: the printed axis widened by its own span on each side."""
    lo, hi = chart.get("axis") or (None, None)
    if lo is None or hi is None or not (math.isfinite(lo) and math.isfinite(hi)) or hi <= lo:
        return -1e6, 1e6
    return lo - (hi - lo), hi + (hi - lo)


# Where a reviewer goes to act on an issue that has no cell, page or slide of its own.
ISSUE_TABS = [
    (("chart_", "correction_"), "charts", "Chart values"),
    (("narrative_", "reviewer_edit_", "manager_doc_"), "narrative", "Narrative"),
    (("figure_", "number_", "footing_", "locator_", "section_", "carried_", "benchmark_", "compliance_",
      "guideline", "allocation_nav", "flash_fund", "flash_sleeve", "flash_row"), "figures", "Figures"),
    (("flash_", "deck_", "prior_pmr_", "ic_", "allocation_", "duplicate_", "client_", "entity_", "style_",
      "reversal_", "approval_"), "inputs", "Inputs"),
]

# Abbreviations spelled out on hover wherever an issue mentions them.
ABBREVIATIONS = {"PMR": "Performance Measurement Report", "QoQ": "quarter over quarter",
                 "IC": "Investment Committee", "NAV": "net asset value", "LTV": "loan to value",
                 "IRR": "internal rate of return"}


def _issue_link(base: str, code: str):
    for prefixes, tab, label in ISSUE_TABS:
        if code.startswith(prefixes):
            return A(label, icon("chevron", 14), href=f"{base}?tab={tab}", cls="link small",
                     style="display:inline-flex;align-items:center;gap:2px")
    return ""


def _abbreviated(text: str) -> list:
    """The text with each known abbreviation marked up, so hovering it shows the full term."""
    parts = re.split(r"\b(" + "|".join(ABBREVIATIONS) + r")\b", text)
    return [Abbr(x, title=ABBREVIATIONS[x]) if x in ABBREVIATIONS else x for x in parts if x]


def _issue_label(code: str) -> str:
    """An issue code as words, e.g. chart_value_implausible -> Chart value implausible."""
    return code.replace("_", " ").capitalize()


def _compliance_pill(status: str | None) -> tuple[str, str]:
    if status is None:
        return "Not checked", "warn"
    return status, "bad" if status.lower().startswith("not") or "breach" in status.lower() else "ok"


def _when_iso(at: str | None, style: str = "when"):
    """An ISO time string from review.json or a run log, shown like every other time in the app."""
    try:
        return _when(datetime.fromisoformat(at), style) if at else ""
    except ValueError:
        return at


def _ev_button(base: str, loc: str, quotes: list[str] = (), label: str = "", text: str | None = None,
               ghost: bool = False):
    from urllib.parse import urlencode

    qs = urlencode([("loc", loc), *[("q", q) for q in quotes if q], ("label", label)])
    look = {"cls": "btn ghost mono"} if ghost else {"cls": "btn mono", "style": "font-size:11px;padding:2px 7px"}
    return Button(icon("file-text", 15 if ghost else 14), text or loc.split("#", 1)[-1] or loc, title=loc,
                  hx_get=f"{base}/evidence?{qs}", hx_target="#evidence", hx_swap="innerHTML",
                  hx_indicator="#evidence", data_pick="1", **look)


def _evidence_aside(hint: str):
    return Div(Div(P("Source evidence", cls="eyebrow"), P(hint, cls="muted"), id="evidence", cls="ev-loading"),
               cls="panel aside")


def _status_card(run: Run, poll: bool):
    lines = [(_when_iso(x["at"], "clock"), f"  {x['message']}\n") for x in (run.log or [])[-10:]]
    attrs = dict(hx_get=f"/reports/{run.report_id}/status", hx_trigger="every 1s", hx_swap="outerHTML") if poll else {}
    title = {"queued": "Waiting for the worker", "running": "Processing"}.get(run.status, run.status.title())
    since = run.started_at or run.created_at
    since = since.replace(tzinfo=UTC) if since and since.tzinfo is None else since
    elapsed = int((now() - since).total_seconds()) if since else 0
    slow = run.status == "running" and elapsed >= 15
    return Div(Div(Div(P(f"{run.kind.title()} run #{run.id}", cls="eyebrow"), H3(title)),
                   Span(f"{run.progress}%", Span(f"  {elapsed // 60}:{elapsed % 60:02d} elapsed", cls="muted small"),
                        cls="num"), cls="flex items-end justify-between"),
               Div(Div(style=f"width:{max(run.progress, 3)}%"),
                   cls="bar" + (" busy" if run.status in ("queued", "running") else ""), style="margin:8px 0"),
               P(run.stage, cls="small"),
               *([P("Still working. A run that needs a new model answer, for example after a chart correction, "
                    "can take a minute or two. Runs that use only saved answers finish in a few seconds.",
                    cls="small muted")] if slow else []),
               Pre(*[part for line in lines for part in line] or ["Waiting for a worker to pick up this run."], cls="log"),
               cls="panel", style="margin-bottom:16px", **attrs)


def _blocks(n: dict) -> list[dict]:
    return [*n.get("commentary", {}).values(), *n.get("strategy", {}).values(),
            *([n["market"]] if n.get("market") and n["market"].get("status") != "no_source" else [])]


def _sentences(block: dict) -> list[dict]:
    return block.get("sentences") or [s for p in block.get("paragraphs", []) for s in p["sentences"]]


def _find_sentence(facts: dict, drafted: str) -> dict | None:
    """The printed sentence whose drafted wording is `drafted`, shaped as it was drafted."""
    for b in _blocks(facts.get("narrative", {})):
        for s in _sentences(b):
            if s.get("edited_from", s["text"]) == drafted:
                return {**s, "text": drafted}
    return None


def _no_draft(active):
    return Div(P("No draft yet." if not active else "The first draft is being built.", cls="muted"), cls="panel")


def _overview(sv, report, facts, manifest, rv, csrf, base, built, active, **_):
    if facts is None:
        return _no_draft(active)
    issues = sorted(facts.get("issues", []), key=lambda i: (i["level"] != "blocker", i["code"]))
    n_block = sum(i["level"] == "blocker" for i in issues)
    v = facts.get("verification", {})
    num, loc = v.get("numbers", {}), v.get("locators", {})
    missing = [e for e in manifest or [] if e["kind"] == "missing"]
    footing = facts.get("footing", {"checks": 0, "failed": []})
    used = sum(d["selected"] for d in facts["files"])
    skipped = len(facts["files"]) - used
    # Without a flash report nothing was built, so a zero here means "not run", not "nothing failed".
    not_run = "Not run: no flash report" if any(i["code"] == "flash_missing" for i in issues) else ""
    tiles = Div(
        kpi("Blockers", n_block, "bad" if n_block else "ok", "must be zero to approve"),
        kpi("Warnings", len(issues) - n_block, "warn" if len(issues) - n_block else "ok", "read before approving"),
        kpi("Numbers checked", num.get("numbers_checked", 0),
            "warn" if not_run else "bad" if num.get("unsourced") else "ok",
            not_run or f"{len(num.get('unsourced', []))} without a source"),
        kpi("Citations re-read", loc.get("checked", 0), "warn" if not_run else "bad" if loc.get("mismatched") else "ok",
            not_run or f"{len(loc.get('mismatched', []))} mismatched"),
        kpi("Footing checks", footing["checks"], "warn" if not_run else "bad" if footing["failed"] else "ok",
            not_run or f"{len(footing['failed'])} failed"),
        kpi("Missing figures", len(missing), "warn" if missing else "ok", not_run or "printed as n/a"),
        kpi("Files used", used, "", A(f"{skipped} skipped: see why", href=f"{base}?tab=inputs", cls="link")
            if skipped else "none skipped"),
        cls="kpis reveal")
    rows = [Tr(Td(pill(i["level"], "bad" if i["level"] == "blocker" else "warn")), Td(*_abbreviated(i["message"])),
               Td(_ev_button(base, i["loc"], label=_issue_label(i["code"])) if "#" in i["loc"]
                  else Span(i["loc"], cls="mono small") if i["loc"] else _issue_link(base, i["code"])))
            for i in issues]
    left = [Div(H2("Validation"), P("Blockers stop approval. Warnings can ship but a reviewer must read them.",
                                    cls="muted"),
                Table(Thead(Tr(Th("Level"), Th("Issue"), Th("Where"))), Tbody(*rows), cls="t") if rows
                else P("No issues.", cls="muted"), cls="panel")]
    if footing["failed"]:
        left.append(Div(H2("Footing failures"), Pre(json.dumps(footing["failed"], indent=1), cls="log"),
                        cls="panel"))
    if missing:
        left.append(Div(H2("Missing data"), P("Figures the inputs did not supply. The report prints a visible "
                                              "marker instead of a guess.", cls="muted"),
                        Table(Thead(Tr(Th("Section"), Th("Item"), Th("Printed"))),
                              Tbody(*[Tr(Td(e["section"]), Td(e["label"]),
                                         Td(e["text"], cls="num") if e["text"] else Td("blank", cls="small muted"))
                                      for e in missing]), cls="t"), cls="panel"))
    if "featured" in facts:
        left.append(Div(H2("Featured funds"), Table(Thead(Tr(Th("Fund"), Th("Sleeve"), Th("Roles"))), Tbody(*[
            Tr(Td(f["fund"]), Td(f["sleeve"]), Td(", ".join(f["roles"]))) for f in facts["featured"]]), cls="t"),
            *[P(x, cls="small muted") for x in facts.get("featured_notes", [])], cls="panel"))
        left.append(Div(H2("Compliance"), Table(Thead(Tr(Th("Guideline"), Th("Limit"), Th("Actual"), Th("Status"))),
                                                Tbody(*[Tr(Td(r["guideline"]), Td(r["limit"]), Td(r["actual_text"], cls="num"),
                                                           Td(pill(*_compliance_pill(r["status"]))))
                                                        for r in facts["compliance"]]), cls="t"), cls="panel"))
        cm = facts["commitments"]
        left.append(Div(H2("Commitments this quarter"), Table(Thead(Tr(Th("Fund"), Th("Amount (USD M)"), Th("Source"))),
                    Tbody(*[Tr(Td(a["fund"]), Td(str(a["amount_musd"]), cls="num r"),
                               Td(_ev_button(base, a["amount_loc"] or a["loc"], label=a["fund"])))
                            for a in cm["new_this_quarter"]]), cls="t")
                    if cm["new_this_quarter"] else P("No new commitments were approved this quarter.", cls="muted"),
                    *([H3("Withdrawn or lapsed this quarter", style="margin-top:20px"),
                       P("Earlier approvals undone this quarter. They are not active commitments.", cls="small muted"),
                       Table(Thead(Tr(Th("Fund"), Th("Status"), Th("Originally approved (USD M)"), Th("Source"))),
                             Tbody(*[Tr(Td(a["fund"]), Td(pill(a["reversal"]["action"], "info")),
                                        Td(str(a["amount_musd"]), cls="num r muted",
                                           style="text-decoration:line-through"),
                                        Td(_ev_button(base, a["reversal"]["loc"], label=a["fund"])))
                                     for a in cm["reversed_this_quarter"]]), cls="t")]
                      if cm["reversed_this_quarter"] else []), cls="panel"))
    return Div(tiles, Div(Div(*left), _evidence_aside("Select a location to see the cell, page or slide it points to."),
                          cls="split", style="margin-top:16px"))


def _figures(sv, report, facts, manifest, base, active, **_):
    if facts is None or manifest is None:
        return _no_draft(active)
    entries = [e for e in manifest if e["kind"] in ("figure", "quote", "missing")]
    rows = []
    for e in entries:
        q = [e["text"]] if e["kind"] == "quote" else [e["text"]]
        src = [Div(e["formula"], cls="small")] if e.get("formula") else []
        src += [_ev_button(base, l, q, label=e["label"] or e["section"]) for l in e["locs"]]
        rows.append(Tr(Td(e["section"], cls="small muted"), Td(e["label"] or Span("(unlabeled)", cls="muted")),
                       Td(e["text"] if e["kind"] != "quote" else Span(e["text"][:140], cls="small"),
                          cls="num" if e["kind"] != "quote" else "",
                          style="color:var(--warn)" if e["kind"] == "missing" else ""),
                       Td(Div(*src, cls="actions") if src else Span("no source", cls="pill bad"))))
    table = Div(H2("Every printed figure"),
                P(f"{len(entries)} figures and quoted passages, each with the workbook cell, PDF page or slide it comes "
                  "from. Computed figures state their formula. Select a source to open it.", cls="muted"),
                Input(type="search", placeholder="Filter by section, item or value", cls="inp",
                      oninput="filterRows(this,'figs')", style="margin:8px 0 12px"),
                Table(Thead(Tr(Th("Section"), Th("Item"), Th("Printed"), Th("Source"))), Tbody(*rows),
                      cls="t", id="figs"), cls="panel")
    return Div(table, _evidence_aside("The cited cell is highlighted with the rows and columns around it, so you can "
                                      "confirm the label as well as the value."), cls="split")


def _narrative(sv, report, facts, rv, csrf, base, active, **_):
    if facts is None:
        return _no_draft(active)
    n = facts.get("narrative", {})
    if not n:
        return Div(P("This draft has no narrative step: the core inputs are incomplete.", cls="muted"), cls="panel")
    action = f"{base}/sentences"
    locked = bool(active)
    cards = []

    def sentence_row(s: dict, i: str):
        drafted = s.get("edited_from", s["text"])
        quotes = s.get("quotes") or ([s["quote"]] if s.get("quote") else [])
        form_id = f"edit-{i}"
        edit = Form(csrf_input(csrf), Input(type="hidden", name="action", value="edit"),
                    Input(type="hidden", name="drafted", value=drafted),
                    Textarea(s["text"], name="text", cls="inp"),
                    P("Reword, shorten or reorder. A number or name that is not in the drafted sentence or its "
                      "evidence is refused.", cls="small muted"),
                    Div(Button("Save edit", cls="btn primary"), Button("Cancel", type="button", cls="btn",
                                                                       onclick=f"toggle('{form_id}')"), cls="actions"),
                    method="post", action=action, id=form_id, hidden=True, style="margin-top:8px")
        buttons = [_ev_button(base, l, quotes, ghost=True) for l in s["locs"]]
        if not locked:
            buttons += [Button(icon("edit", 15), "Edit", type="button", cls="btn ghost", onclick=f"toggle('{form_id}')"),
                        _post_button(action, "Exclude", csrf, "ghost", mark="blocked", action="exclude", drafted=drafted)]
            if s.get("edited_from"):
                buttons.append(_post_button(action, "Revert edit", csrf, "ghost", mark="undo", action="revert",
                                            drafted=drafted))
        return Div(Div(s["text"], cls="txt"),
                   *([P("Edited by the reviewer. Drafted as: ", Span(s["edited_from"], cls="quote"), cls="small muted")]
                     if s.get("edited_from") else []),
                   Div(*buttons, cls="actions ghost"), *([] if locked else [edit]), cls="sentence")

    k = 0
    named = ([("Commentary", f, b) for f, b in n.get("commentary", {}).items()] +
             [("Strategy", f, b) for f, b in n.get("strategy", {}).items()] +
             ([("Narrative", "Market update", n["market"])] if n.get("market", {}).get("status") != "no_source"
              else []))
    for kind, title, b in named:
        body = []
        for s in _sentences(b):
            k += 1
            body.append(sentence_row(s, str(k)))
        groups = [b] if "sentences" in b else b.get("paragraphs", [])
        excluded = [x for gr in groups for x in gr.get("excluded", [])]
        status = {"generated": ("drafted and checked", "ok"), "rejected": ("placeholder: nothing passed", "bad"),
                  "unavailable": ("placeholder: no model answer", "warn")}.get(b["status"], (b["status"], "info"))
        extra = []
        if excluded:
            extra.append(Div(H3("Excluded by the reviewer"), *[
                Div(Div(x["text"], cls="txt"), Div(*([] if locked else [_post_button(action, "Restore", csrf, "ghost",
                                                                                     mark="undo", action="restore",
                                                                                     drafted=x["text"])]),
                                                   cls="actions ghost"), cls="sentence ex") for x in excluded],
                             style="margin-top:16px"))
        if b.get("dropped"):
            extra.append(Details_(f"Drafted but dropped by the source checks ({len(b['dropped'])})", [
                Div(Div(d["text"], cls="txt"), P("; ".join(d["problems"]), cls="small", style="color:var(--bad)"),
                    cls="sentence ex") for d in b["dropped"]]))
        cards.append(Div(Div(Div(P(kind, cls="eyebrow"), H2(title, style="margin:4px 0 0")),
                             pill(status[0], status[1]), cls="section-head"),
                         Div(b.get("source", ""), cls="section-src"),
                         *(body or [P("No sentence printed; the report shows a marked placeholder.", cls="muted")]),
                         *extra, cls="section"))
    stale = [t for t in rv["excluded_sentences"] if not any(
        t == x["text"] for b in _blocks(n) for gr in ([b] if "sentences" in b else b.get("paragraphs", []))
        for x in gr.get("excluded", []))]
    if stale:
        cards.append(Div(H2("Exclusions not in this draft"), P("These excluded sentences were not drafted this time, "
                                                               "or their paragraph is now empty.", cls="muted"),
                         *[Div(Div(t, cls="txt"), Div(_post_button(action, "Restore", csrf, "ghost", mark="undo",
                                                                   action="restore", drafted=t), cls="actions ghost"),
                               cls="sentence ex") for t in stale], cls="section"))
    intro = P("The model drafted these sentences from the manager reports and the outlook deck. Code checked every "
              "quote against its source and every number and name against the cited evidence. You decide what goes "
              "in the report.", cls="muted", style="margin-bottom:24px;line-height:22px")
    return Div(Div(intro, *cards), _evidence_aside("Open a sentence's evidence to see the page or slide with the "
                                                   "quoted passage highlighted."), cls="split")


def Details_(summary: str, children: list):
    from fasthtml.common import Details, Summary

    return Details(Summary(summary, cls="small", style="margin-top:10px"), *children)


def _charts(sv, report, facts, rv, csrf, base, active, **_):
    if facts is None:
        return _no_draft(active)
    charts = facts.get("narrative", {}).get("charts", [])
    if not charts:
        return Div(P("This draft read no chart values.", cls="muted"), cls="panel")
    cited = set(facts["narrative"].get("market", {}).get("cited_values", []))
    images = {Path(k).stem: k.rsplit("/", 1)[1] for k in sv.storage.list(_out_key(report, "charts") or "")}
    out = [Div(P("These values exist only as pixels in the deck's chart images, so the model read them. Code checked "
                 "each for plausibility, but only a person can confirm them. Values marked cited appear in the market "
                 "update. A correction replaces the model's reading in the next draft.", cls="muted"), cls="panel")]
    for ch in charts:
        failed = [x for x in ch.get("checks", []) if not x["ok"]]
        rows = []
        for sr in ch["series"]:
            for p in sr["points"]:
                corrected = p["id"] in rv["chart_value_corrections"]
                form = Form(csrf_input(csrf), Input(type="hidden", name="point", value=p["id"]),
                            Input(type="text", name="value", value=str(rv["chart_value_corrections"].get(p["id"], "")),
                                  placeholder="correct to", cls="inp num", style="width:90px;padding:3px 6px",
                                  inputmode="decimal", disabled=bool(active)),
                            Button("Save", cls="btn", disabled=bool(active)),
                            *([Button("Clear", name="clear", value="1", cls="btn")] if corrected and not active else []),
                            method="post", action=f"{base}/charts", cls="flex gap-2 items-center")
                rows.append(Tr(Td(sr["name"], cls="small"), Td(p["category"]),
                               Td(str(p["value"]), cls="num r"),
                               Td(Span(p["read"], cls="small muted"),
                                  *([Div(f"model read {p['model_value']}", cls="small muted")] if "model_value" in p else [])),
                               Td(pill("cited", "accent") if p["id"] in cited else ""), Td(form)))
        img = images.get(ch["id"])
        out.append(Div(
            Div(Div(H2(ch.get("title") or ch["slide_title"]), P(ch["loc"], cls="mono small muted")),
                pill(ch["status"], "ok" if ch["status"] == "read" else "warn"), cls="flex justify-between items-start"),
            *[alert(f"Check failed: {x['check']} ({x['detail']})", tone="warn") for x in failed],
            Div(Div(Img(src=f"{base}/out/charts/{img}", alt=f"Chart from slide {ch['slide']}", cls="ev-page")
                    if img else P("Image not available.", cls="muted")),
                Div(Table(Thead(Tr(Th("Series"), Th("Category"), Th("Value"), Th("Read as"), Th(""), Th("Correction"))),
                          Tbody(*rows), cls="t"), style="overflow-x:auto"),
                cls="grid gap-4", style="grid-template-columns:minmax(0,1fr) minmax(0,1.3fr)"),
            cls="panel"))
    return Div(*out)


def _pdf(sv, report, facts, base, active, **_):
    if facts is None or not facts.get("verification"):
        return _no_draft(active)
    name = facts["verification"]["pdf"]
    return Div(Div(Div(H2("Draft PDF"), P("Marked DRAFT FOR REVIEW on every page. The final PDF is written only after "
                                          "approval.", cls="muted")),
                   A(icon("download"), "Download", href=f"{base}/out/{name}?download=1", cls="btn"), cls="flex justify-between items-start"),
               Iframe(src=f"{base}/out/{name}", style="width:100%;height:80vh;border:1px solid var(--rule)",
                      title="Draft PDF"), cls="panel")


def _approval_problems(report: Report, built: Run | None, facts: dict | None, active) -> list[str]:
    problems = []
    if built is None or facts is None:
        problems.append("there is no draft")
        return problems
    if active:
        problems.append("a run is in progress")
    if built.n_blockers:
        problems.append(f"{built.n_blockers} blocker(s) are open")
    if report.review_rev > built.review_rev:
        problems.append("reviewer changes are not yet in the draft")
    return problems


def _approval(sv, report, facts, rv, csrf, base, built, active, **_):
    if facts is None:
        return _no_draft(active)
    current = review.content_hash(facts)
    pending = _pending(report, built)
    # Unapplied reviewer changes put the approval on hold: what was approved is not what the next draft says.
    approved = rv.get("approved_hash") == current and not pending and not active
    problems = _approval_problems(report, built, facts, active)
    checks = [("A draft exists and finished", built is not None),
              ("No blockers are open", not (built and built.n_blockers)),
              ("Every reviewer change is in the draft", not (built and report.review_rev > built.review_rev)),
              ("No run is in progress", not active)]
    gate = Div(H2("Approval gate"), P("Approval records your name, the time and a fingerprint (SHA-256) of everything "
                                      "the report says. Any later change to an input, figure, sentence or correction "
                                      "changes the fingerprint and voids the approval.", cls="muted"),
               Ul(*[Li(pill("pass" if ok else "fail", "ok" if ok else "bad"), Span(f"  {t}")) for t, ok in checks],
                  style="list-style:none;padding:0;line-height:2.2"), cls="panel")
    final_runs = [r for r in report.runs if r.kind == "finalize" and r.final_pdf]
    final_now = None if pending else next((r for r in final_runs if r.content_hash == current), None)
    if approved:
        record = Div(H2("Approved"), P(f"By {rv['reviewer']} on ", _when_iso(rv['approved_at'])),
                     P("Content hash ", Span(current, cls="mono", style="word-break:break-all"), cls="small muted"),
                     Div(*([] if final_now else [_post_button(f"{base}/finalize", "Generate the final PDF", csrf,
                                                              "primary lg")]),
                         _post_button(f"{base}/unapprove", "Withdraw approval", csrf, "danger"), cls="actions"),
                     P("Finalizing rebuilds the report from the stored inputs and writes the final PDF only if the "
                       "content still matches this approval, then verifies the final PDF again.", cls="small muted"),
                     cls="panel", style="border-top:3px solid var(--accent)")
    else:
        if rv.get("approved_hash") == current and pending:
            stale = alert(f"The approval by {rv['reviewer']} on ", _when_iso(rv['approved_at']), " is on hold. "
                          "Reviewer changes saved since then are not in the draft yet. Regenerate the draft and approve the "
                          "new content.", tone="warn")
        elif rv.get("approved_hash"):
            stale = alert(f"An earlier approval by {rv['reviewer']} on ", _when_iso(rv['approved_at']),
                          " no longer matches this draft. The report changed after it was approved.", tone="warn")
        elif v := rv.get("voided"):
            stale = alert(f"The approval by {v['approved_by']} on ", _when_iso(v['approved_at']),
                          f" was voided when run #{v['run']} built a draft with different content. Approving again records a new "
                          "approval, even if the content later returns to what was approved before.", tone="warn")
        else:
            stale = ""
        record = Div(H2("Approve this draft"), stale,
                     Form(csrf_input(csrf),
                          *[Label(Input(type="checkbox", name=f"attest_{k}", value="1", required=True), f"  {t}",
                                  style="display:block;margin:8px 0") for k, t in ATTESTATIONS],
                          Div(Button(f"Approve as {_display_user()}", cls="btn primary lg", disabled=bool(problems)),
                              style="margin-top:14px"),
                          *([P("Not available: " + "; ".join(problems) + ".", cls="small", style="color:var(--bad)")]
                            if problems else []),
                          method="post", action=f"{base}/approve"),
                     cls="panel")
    downloads = [("Draft PDF", facts["verification"]["pdf"]) if facts.get("verification") else None,
                 ("facts.json", "facts.json"), ("manifest.json", "manifest.json"), ("review.json", "review.json"),
                 ("Static review page", "review.html")]
    files = Div(H2("Outputs"), Ul(*[Li(A(lab, href=f"{base}/out/{name}?download=1", cls="link")) for x in downloads if x
                                    for lab, name in [x]],
                                  *[Li(A(f"Final PDF (run #{r.id})", href=f"{base}/out/{r.final_pdf}", cls="link"),
                                       Span("  current" if r is final_now else
                                            "  waiting on unapplied reviewer changes" if r.content_hash == current
                                            else "  superseded by a later draft", cls="small muted"))
                                    for r in final_runs],
                                  style="line-height:2"), cls="panel")
    final = (Div(H2("Final report"), P("The final PDF matches the approved content and passed verification."),
                 A("Open the final PDF", href=f"{base}/out/{final_now.final_pdf}", cls="btn primary lg"),
                 cls="panel", style="border-top:3px solid var(--ink)") if final_now else "")
    return Div(Div(final, record, gate), files, cls="split")


def _display_user() -> str:
    return g.user or "reviewer"


def _inputs(sv, report, facts, csrf, base, active, **_):
    decisions = {d["file"]: d for d in (facts or {}).get("files", [])}
    rows = []
    for f in report.files:
        d = decisions.get(f.path)
        rows.append(Tr(Td(Span(f.path, cls="mono small")),
                       Td(pill("used", "ok") if d and d["selected"] else pill("skipped", "info") if d else ""),
                       Td(d["reason"] if d else Span("not in the last draft", cls="muted"), cls="small"),
                       Td(f"{f.size / 1024:,.0f} KB", cls="num r small"),
                       Td(Span(f.sha256[:12], cls="mono small", title=f.sha256)),
                       Td(f.uploaded_by, cls="small"),
                       Td("" if active else _post_button(f"{base}/files/delete", "Remove", csrf, "danger", path=f.path))))
    upload = Form(csrf_input(csrf), H3("Add or replace files"),
                  P("A file with the same name replaces the earlier one. Regenerate the draft afterwards.",
                    cls="small muted"),
                  _drop_zone("Folder", "Files"),
                  Div(Button(icon("upload"), "Upload", cls="btn primary", disabled=bool(active)),
                      _post_button(f"{base}/runs", "Regenerate draft", csrf, "", mark="refresh"), cls="actions",
                      style="margin-top:10px"),
                  method="post", action=f"{base}/files", enctype="multipart/form-data")
    return Div(Div(H2("Source documents"),
                   P("Every uploaded file with the discovery step's decision. Discovery reads content, not names, so "
                     "another client's flash or an earlier quarter's deck is skipped with its reason.", cls="muted"),
                   Div(Table(Thead(Tr(Th("File"), Th("Decision"), Th("Reason"), Th("Size"), Th("SHA-256"),
                                      Th("Uploaded by"), Th(""))), Tbody(*rows), cls="t"), style="overflow-x:auto"),
                   cls="panel"), Div(upload, cls="panel"))


def _audit(sv, report, **_):
    with sv.db.session() as s:
        events = s.scalars(select(AuditEvent).where(AuditEvent.report_id == report.id)
                           .order_by(AuditEvent.id.desc())).all()
        runs = s.scalars(select(Run).where(Run.report_id == report.id).order_by(Run.id.desc())).all()
    ev_rows = [Tr(Td(_when(e.at, "stamp"), cls="num small"), Td(e.actor, cls="small"),
                  Td(Span(e.action, cls="mono small")), Td(f"#{e.run_id}" if e.run_id else "", cls="num small"),
                  Td(Details_(_summary(e.detail), [Pre(json.dumps(e.detail, indent=1, default=str), cls="log")])
                     if e.detail else "")) for e in events]
    run_rows = [Tr(Td(f"#{r.id}", cls="num"), Td(r.kind), Td(pill(r.status, {"succeeded": "ok", "failed": "bad"}
                                                                 .get(r.status, "info"))),
                   Td(r.requested_by, cls="small"),
                   Td(_when(r.started_at, "clock"), cls="num small"),
                   Td(f"{(r.finished_at - r.started_at).total_seconds():.1f}s" if r.finished_at and r.started_at else "",
                      cls="num small r"),
                   Td(Span((r.content_hash or "")[:12], cls="mono small")),
                   Td(r.error or "", cls="small")) for r in runs]
    return Div(Div(H2("Runs"), Table(Thead(Tr(Th("Run"), Th("Kind"), Th("Status"), Th("By"), Th("Started"),
                                              Th("Took"), Th("Content hash"), Th("Note"))), Tbody(*run_rows), cls="t"),
                   cls="panel"),
               Div(H2("Audit trail"), P("Every upload, run, reviewer change and approval, oldest at the bottom. Times "
                                        "are in your device's time zone, or US Eastern if the browser does not report one. "
                                        "Rows are only ever added.", cls="muted"),
                   Table(Thead(Tr(Th("When"), Th("Who"), Th("Action"), Th("Run"), Th("Detail"))), Tbody(*ev_rows),
                         cls="t"), cls="panel"))


def _summary(detail: dict) -> str:
    keys = [k for k in ("path", "drafted", "point", "message", "content_hash", "error", "blockers") if detail.get(k)
            is not None]
    # A SHA-256 fingerprint is only useful whole; other values are shortened to keep the row on one line.
    return "; ".join(f"{k}: {detail[k] if k == 'content_hash' else str(detail[k])[:120]}" for k in keys[:2]) or "details"


def main() -> None:
    app = create_app()
    app.run(host="127.0.0.1", port=5001, debug=False)


if __name__ == "__main__":
    main()
