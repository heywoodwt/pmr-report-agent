"""The web front end: upload, process, review, approve and finalize through the browser routes.

Runs the real pipeline on the committed inputs in cache-only mode, with the worker drained
synchronously, so no test calls the model or waits on a thread.
"""

import json
import re

import pytest

pytest.importorskip("flask")
pytest.importorskip("monsterui")

from pmr import review  # noqa: E402
from pmr_web.app import create_app  # noqa: E402
from pmr_web.config import Settings  # noqa: E402
from pmr_web.db import AuditEvent, Report  # noqa: E402
from sqlalchemy import select  # noqa: E402
from pmr_web.worker import drain  # noqa: E402


@pytest.fixture(scope="module")
def web(tmp_path_factory):
    """One app with one CPERS 4Q25 report drafted from the sample inputs."""
    data = tmp_path_factory.mktemp("webdata")
    settings = Settings.from_env({"PMR_DATA_DIR": str(data), "PMR_NO_LLM": "1", "PMR_EMBEDDED_WORKER": "0"})
    app = create_app(settings, start_worker=False)
    client = app.test_client()
    client.get("/signin")
    token = _token(client)
    client.post("/signin", data={"name": "Test Reviewer", "csrf": token})
    r = client.post("/reports", data={"client": "CPERS", "quarter": "4Q25", "sample": "1", "csrf": token})
    assert r.status_code == 302
    sv = app.extensions["pmr"]
    assert drain(sv) == 1
    return app, client, sv


def _token(client) -> str:
    with client.session_transaction() as s:
        return s["csrf"]


def _post(client, url, **data):
    return client.post(url, data={"csrf": _token(client), **data})


def _links_to(client, url, rid) -> bool:
    """Whether the page links to the report, whose URL is a readable slug ending in its id."""
    return re.search(rf'href="/reports/[a-z0-9-]*-{rid}"', client.get(url).get_data(as_text=True)) is not None


def _facts(sv) -> dict:
    keys = [k for k in sv.storage.list("reports/1/output") if k.endswith("/facts.json")]
    return json.loads(sv.storage.read(keys[0]))


def _review(sv) -> dict:
    keys = [k for k in sv.storage.list("reports/1/output") if k.endswith("/review.json")]
    return json.loads(sv.storage.read(keys[0]))


def test_version_reports_the_build_commit_without_sign_in(web, monkeypatch):
    app, _, _ = web
    monkeypatch.setenv("PMR_VERSION", "abc123")
    r = app.test_client().get("/version")
    assert r.status_code == 200 and r.get_json() == {"version": "abc123"}


def test_sign_in_is_required(web):
    app, _, _ = web
    assert app.test_client().get("/").status_code == 302


def test_sign_out_clears_the_name_and_returns_to_sign_in(web):
    app, _, _ = web
    client = app.test_client()
    client.get("/signin")
    client.post("/signin", data={"name": "First Name", "csrf": _token(client)})
    assert "Sign out" in client.get("/").get_data(as_text=True)
    r = _post(client, "/signout")
    assert r.status_code == 302 and r.headers["Location"].endswith("/signin")
    assert client.get("/").status_code == 302
    client.get("/signin")
    client.post("/signin", data={"name": "Second Name", "csrf": _token(client)})
    page = client.get("/").get_data(as_text=True)
    assert "Second Name" in page and "First Name" not in page


def test_an_access_code_guards_the_name_sign_in(tmp_path):
    settings = Settings.from_env({"PMR_DATA_DIR": str(tmp_path), "PMR_NO_LLM": "1", "PMR_ACCESS_CODE": "letmein"})
    client = create_app(settings, start_worker=False).test_client()
    page = client.get("/signin").get_data(as_text=True)
    assert 'name="code"' in page
    assert ".field input[type=password]" in page, "the code field must be styled like the name field"
    client.post("/signin", data={"name": "Guest", "code": "wrong", "csrf": _token(client)})
    assert client.get("/").status_code == 302
    client.post("/signin", data={"name": "Guest", "csrf": _token(client)})
    assert client.get("/").status_code == 302
    client.post("/signin", data={"name": "Guest", "code": "letmein", "csrf": _token(client)})
    assert client.get("/").status_code == 200


def test_authentication_headers_are_trusted_only_when_app_service_authentication_is_on(tmp_path):
    base = {"PMR_DATA_DIR": str(tmp_path), "PMR_NO_LLM": "1", "WEBSITE_SITE_NAME": "app-pmr"}
    assert not Settings.from_env(base).trust_easy_auth
    assert Settings.from_env({**base, "WEBSITE_AUTH_ENABLED": "True"}).trust_easy_auth
    client = create_app(Settings.from_env(base), start_worker=False).test_client()
    assert client.get("/", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "spoofed"}).status_code == 302


def test_post_without_csrf_token_is_refused(web):
    _, client, _ = web
    assert client.post("/reports/1/runs").status_code == 400


def test_first_draft_is_built_and_every_tab_renders(web):
    _, client, sv = web
    facts = _facts(sv)
    assert facts["client"]["code"] == "CPERS"
    assert facts["verification"]["numbers"]["unsourced"] == []
    for tab in ("overview", "figures", "narrative", "charts", "pdf", "approval", "inputs", "audit"):
        r = client.get(f"/reports/1?tab={tab}")
        assert r.status_code == 200, tab
    # The overview shows the verification counts the pipeline recorded.
    page = client.get("/reports/1").get_data(as_text=True)
    assert str(facts["verification"]["numbers"]["numbers_checked"]) in page


def test_stored_facts_hash_matches_the_run(web):
    """Approval hashes the stored facts.json; finalization re-hashes in memory. They must agree."""
    _, _, sv = web
    from pmr_web.db import Run

    with sv.db.session() as s:
        run = s.get(Run, 1)
    assert review.content_hash(_facts(sv)) == run.content_hash


def test_evidence_shows_the_cited_cell_and_refuses_paths_outside_inputs(web):
    _, client, _ = web
    r = client.get("/reports/1/evidence", query_string={"loc": "flash_4Q25.xlsx#CashActivity(Agg)!F11"})
    assert b"ev-hit" in r.data
    r = client.get("/reports/1/evidence", query_string={"loc": "../../../etc/passwd#p1"})
    assert b"not among this report" in r.data


def test_edit_adding_a_number_is_refused(web):
    _, client, sv = web
    s = _facts(sv)["narrative"]["commentary"]
    drafted = next(iter(s.values()))["sentences"][0]["text"]
    _post(client, "/reports/1/sentences", action="edit", drafted=drafted, text=drafted + " Occupancy was 97%.")
    assert drafted not in _review(sv)["edited_sentences"]


def test_edit_with_markup_is_refused(web):
    _, client, sv = web
    drafted = next(iter(_facts(sv)["narrative"]["commentary"].values()))["sentences"][0]["text"]
    marked = drafted.replace("One Financial Plaza", "<i>One Financial Plaza</i><svg onload=alert()>")
    _post(client, "/reports/1/sentences", action="edit", drafted=drafted, text=marked)
    assert drafted not in _review(sv)["edited_sentences"]


@pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-Infinity", "1e400", "abc", "1e9"])
def test_chart_correction_must_be_a_finite_number_in_range(web, value):
    _, client, sv = web
    point = next(p["id"] for ch in _facts(sv)["narrative"]["charts"] for sr in ch["series"] for p in sr["points"])
    _post(client, "/reports/1/charts", point=point, value=value)
    assert point not in _review(sv)["chart_value_corrections"]


def test_audit_trail_refuses_values_json_cannot_hold(web):
    _, _, sv = web
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError):
            sv.db.audit(1, "Test Reviewer", "test_event", value=bad)


def test_evidence_finds_a_sheet_whose_name_has_a_trailing_space(web):
    """The IC log's tab is saved as "Log " and cited as "Log"."""
    _, client, _ = web
    r = client.get("/reports/1/evidence", query_string={"loc": "ic_log_2025.xlsx#Log!G9"})
    assert b"ev-hit" in r.data and b"is not in this workbook" not in r.data


def test_reviewer_changes_block_approval_until_regenerated_then_apply(web):
    _, client, sv = web
    blocks = list(_facts(sv)["narrative"]["commentary"].values())
    excluded = blocks[0]["sentences"][-1]["text"]
    drafted = blocks[0]["sentences"][0]["text"]
    reworded = re.sub(r"^During the quarter, ", "In the quarter, ", drafted)
    assert reworded != drafted
    _post(client, "/reports/1/sentences", action="exclude", drafted=excluded)
    _post(client, "/reports/1/sentences", action="edit", drafted=drafted, text=reworded)

    # Saved but not yet drafted: approval is refused.
    r = _post(client, "/reports/1/approve", attest_charts="1", attest_narrative="1", attest_issues="1")
    assert _review(sv)["approved_hash"] is None
    assert r.status_code == 302

    _post(client, "/reports/1/runs")
    assert drain(sv) == 1
    first = next(iter(_facts(sv)["narrative"]["commentary"].values()))
    texts = [x["text"] for x in first["sentences"]]
    assert excluded not in texts
    assert reworded in texts
    assert first["sentences"][0]["edited_from"] == drafted


def test_approval_requires_every_attestation(web):
    _, client, sv = web
    _post(client, "/reports/1/approve", attest_charts="1")
    assert _review(sv)["approved_hash"] is None


def test_approve_then_finalize_writes_the_final_pdf(web):
    _, client, sv = web
    _post(client, "/reports/1/approve", attest_charts="1", attest_narrative="1", attest_issues="1")
    rv = _review(sv)
    assert rv["reviewer"] == "Test Reviewer"
    assert rv["approved_hash"] == review.content_hash(_facts(sv))

    _post(client, "/reports/1/finalize")
    assert drain(sv) == 1
    finals = [k for k in sv.storage.list("reports/1/output") if re.search(r"/PMR_CPERS_4Q25\.pdf$", k)]
    assert finals
    page = client.get("/reports/1?tab=approval").get_data(as_text=True)
    assert "Open the final PDF" in page
    actions = client.get("/reports/1?tab=audit").get_data(as_text=True)
    for action in ("report_created", "file_uploaded", "sentence_exclude", "sentence_edit", "approved",
                   "finalize_finished"):
        assert action in actions


def test_a_change_after_approval_voids_it(web):
    _, client, sv = web
    s = next(iter(_facts(sv)["narrative"]["commentary"].values()))["sentences"][0]
    _post(client, "/reports/1/sentences", action="revert", drafted=s["edited_from"])
    # Saved but not drafted: the approval is on hold, and nothing claims the final PDF is current.
    page = client.get("/reports/1?tab=approval").get_data(as_text=True)
    assert "is on hold" in page and "Open the final PDF" not in page and "  current" not in page
    _post(client, "/reports/1/runs")
    drain(sv)
    rv = _review(sv)
    # The rebuild voids the approval for good and says so, rather than leaving a stale hash that could match again.
    assert rv["approved_hash"] is None and rv["voided"]["approved_hash"] != review.content_hash(_facts(sv))
    page = client.get("/reports/1?tab=approval").get_data(as_text=True)
    assert "was voided" in page
    with sv.db.session() as s:
        assert "approval_voided" in [e.action for e in s.scalars(select(AuditEvent))]


def test_archive_hides_a_report_restore_brings_it_back_and_delete_needs_archive_first(web):
    _, client, sv = web
    client.post("/reports", data={"client": "CPERS", "quarter": "4Q25", "sample": "1", "csrf": _token(client)})
    assert drain(sv) == 1
    rid = 2
    assert _links_to(client, "/", rid)

    # Delete is refused until the report is archived.
    _post(client, f"/reports/{rid}/delete")
    assert client.get(f"/reports/{rid}").status_code == 200

    _post(client, f"/reports/{rid}/archive")
    assert not _links_to(client, "/", rid)
    assert _links_to(client, "/?archived=1", rid)
    assert "was archived" in client.get(f"/reports/{rid}").get_data(as_text=True)

    _post(client, f"/reports/{rid}/restore")
    assert _links_to(client, "/", rid)


def test_delete_removes_files_drafts_and_runs_but_keeps_the_audit_trail(web):
    _, client, sv = web
    from pmr_web.db import InputFile, Run

    rid = 2
    assert sv.storage.list(f"reports/{rid}")
    _post(client, f"/reports/{rid}/archive")
    _post(client, f"/reports/{rid}/delete")
    assert sv.storage.list(f"reports/{rid}") == []
    assert client.get(f"/reports/{rid}").status_code == 404
    assert f'href="/reports/{rid}"' not in client.get("/?archived=1").get_data(as_text=True)
    with sv.db.session() as s:
        assert not s.scalars(select(Run).where(Run.report_id == rid)).all()
        assert not s.scalars(select(InputFile).where(InputFile.report_id == rid)).all()
        actions = [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.report_id == rid))]
    assert "report_deleted" in actions and "report_created" in actions
    # The other report is untouched.
    assert client.get("/reports/1").status_code == 200


def test_a_zip_upload_is_unpacked_into_its_documents(web):
    import io
    import zipfile

    from pmr_web.db import InputFile
    from tests.conftest import INPUTS

    _, client, sv = web
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.write(INPUTS / "flash_4Q25.xlsx", "drop/flash_4Q25.xlsx")
        zf.write(INPUTS / "ic_log_2025.xlsx", "drop/logs/ic_log_2025.xlsx")
        # The prior PMR defines the code CPERS, so the client check before the create passes.
        zf.write(INPUTS / "prior_pmr_3Q25.pdf", "drop/prior_pmr_3Q25.pdf")
        zf.writestr("__MACOSX/drop/._flash_4Q25.xlsx", b"x")
        zf.writestr("drop/inner.zip", b"x")
        zf.writestr("drop/run.exe", b"x")
    r = client.post("/reports", data={"client": "CPERS", "quarter": "4Q25", "csrf": _token(client),
                                      "files": (io.BytesIO(buf.getvalue()), "drop.zip")},
                    content_type="multipart/form-data")
    rid = int(r.headers["Location"].rsplit("-", 1)[-1])
    with sv.db.session() as s:
        paths = sorted(f.path for f in s.scalars(select(InputFile).where(InputFile.report_id == rid)))
        actions = [e.action for e in s.scalars(select(AuditEvent).where(AuditEvent.report_id == rid))]
    assert paths == ["drop/flash_4Q25.xlsx", "drop/logs/ic_log_2025.xlsx", "drop/prior_pmr_3Q25.pdf"]
    assert "archive_unpacked" in actions


def test_a_client_and_quarter_the_files_cannot_report_on_is_refused_with_what_they_hold(web):
    _, client, sv = web
    with sv.db.session() as s:
        before = len(s.scalars(select(Report)).all())
    r = client.post("/reports", data={"client": "PMR", "quarter": "4Q26", "sample": "1", "csrf": _token(client)})
    assert r.status_code == 302 and "/reports/new?" in r.headers["Location"]
    with sv.db.session() as s:
        assert len(s.scalars(select(Report)).all()) == before, "no report is created"
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "No flash report in these files is for PMR 4Q26" in page
    assert "CPERS (Cascadia Public Employees' Retirement System): 4Q25" in page
    assert 'value="PMR"' in page and '<option value="4Q26" selected>' in page


def test_a_corrupt_zip_is_skipped(web):
    import io

    _, client, _ = web
    r = client.post("/reports", data={"client": "CPERS", "quarter": "4Q25", "sample": "1", "csrf": _token(client),
                                      "files": (io.BytesIO(b"not a zip"), "bad.zip")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "could not be unpacked" in r.get_data(as_text=True)


def test_a_zip_added_on_the_inputs_tab_is_unpacked(web):
    import io
    import zipfile

    from pmr_web.db import InputFile

    _, client, sv = web
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("extra/notes.txt", b"notes")
    client.post("/reports/1/files", data={"csrf": _token(client), "files": (io.BytesIO(buf.getvalue()), "extra.zip")},
                content_type="multipart/form-data")
    with sv.db.session() as s:
        paths = {f.path for f in s.scalars(select(InputFile).where(InputFile.report_id == 1))}
    assert "extra/notes.txt" in paths and "extra.zip" not in paths


@pytest.mark.parametrize("url", ["/reports/new", "/reports/1?tab=inputs"])
def test_upload_forms_have_a_drop_zone(web, url):
    _, client, _ = web
    page = client.get(url).get_data(as_text=True)
    assert "data-drop" in page and "Drag and drop files or folders here" in page


def test_a_dropped_folder_keeps_its_layout_and_loose_files_keep_their_names(web):
    import io

    from pmr_web.db import InputFile

    _, client, sv = web
    # The drop script sends a dropped folder's files as "<folder>/<sub>/<file>" under "folder" and loose files under
    # "files", each drop in its own hidden input, so the form can carry several inputs with the same name.
    r = client.post("/reports", data={"client": "CPERS", "quarter": "4Q25", "sample": "1", "csrf": _token(client),
                                      "folder": [(io.BytesIO(b"a"), "drop_1Q27/logs/notes.txt"),
                                                 (io.BytesIO(b"b"), "drop_1Q27/.DS_Store")],
                                      "files": [(io.BytesIO(b"c"), "extra.csv")]},
                    content_type="multipart/form-data")
    rid = int(r.headers["Location"].rsplit("-", 1)[-1])
    with sv.db.session() as s:
        paths = sorted(f.path for f in s.scalars(select(InputFile).where(InputFile.report_id == rid)))
    # The sample drop folder is there so the client check passes; the dropped files keep their layout.
    assert {"extra.csv", "logs/notes.txt"} <= set(paths) and not any(".DS_Store" in p for p in paths)


@pytest.mark.parametrize(("utc", "style", "shown"), [
    ("2026-11-02T14:08:05+00:00", "when", "2 Nov 2026, 09:08 EST"),
    ("2026-07-02T14:08:05+00:00", "stamp", "2026-07-02 10:08:05 EDT"),
    ("2026-07-02T14:08:05", "clock", "10:08:05 EDT"),  # SQLite returns naive datetimes; they are UTC
])
def test_times_are_shown_in_eastern_time_with_the_utc_instant_for_the_browser(utc, style, shown):
    from datetime import datetime

    from fasthtml.common import to_xml

    from pmr_web.app import _when

    html = to_xml(_when(datetime.fromisoformat(utc), style))
    assert f">{shown}</time>" in html
    assert f'datetime="{utc[:19]}+00:00"' in html and f'data-tz="{style}"' in html


def test_no_page_shows_utc(web):
    _, client, _ = web
    for tab in ("overview", "approval", "audit"):
        page = client.get(f"/reports/1?tab={tab}").get_data(as_text=True)
        assert " UTC" not in re.sub(r"<script.*?</script>", "", page, flags=re.DOTALL), tab
        assert "<time" in page or tab == "approval", tab


def test_report_urls_name_the_client_quarter_and_draft_time_and_any_slug_with_the_id_opens_it(web):
    _, client, _ = web
    page = client.get("/").get_data(as_text=True)
    slug = re.search(r'href="/reports/(cpers-4q25-draft-\d{4}-\d{2}-\d{2}-\d{4}-e[sd]t-1)"', page)
    assert slug, "the report list links to the readable URL"
    for url in (f"/reports/{slug.group(1)}", "/reports/1", "/reports/cpers-4q25-draft-2020-01-01-0000-est-1"):
        r = client.get(url)
        assert r.status_code == 200 and f'"/reports/{slug.group(1)}"' in r.get_data(as_text=True)
    assert client.get("/reports/cpers-4q25").status_code == 404


def test_pages_use_the_logo_as_the_favicon(web):
    _, client, _ = web
    assert '<link rel="icon" type="image/svg+xml" href="/static/logo.svg">' in client.get("/").get_data(as_text=True)
    assert client.get("/static/logo.svg").status_code == 200
