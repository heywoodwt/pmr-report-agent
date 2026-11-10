"""The embedded worker loop: a database error must not end it, and /health must report a dead one."""

import threading

import pytest

pytest.importorskip("flask")
pytest.importorskip("monsterui")

from pmr_web.app import create_app  # noqa: E402
from pmr_web.config import Settings  # noqa: E402
from pmr_web.db import Database  # noqa: E402
from pmr_web.worker import Services, work_forever  # noqa: E402


def _services(tmp_path) -> Services:
    return Services.start(Settings.from_env({"PMR_DATA_DIR": str(tmp_path), "PMR_NO_LLM": "1",
                                             "PMR_EMBEDDED_WORKER": "0"}))


def test_the_worker_keeps_polling_after_the_database_raises(tmp_path, monkeypatch):
    sv = _services(tmp_path)
    stop = threading.Event()
    calls = []

    def flaky_claim():
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("server closed the connection unexpectedly")
        stop.set()

    monkeypatch.setattr(sv.db, "claim_next_run", flaky_claim)
    work_forever(sv, stop, poll_seconds=0)
    assert len(calls) == 2


def test_health_fails_when_the_embedded_worker_has_stopped(tmp_path):
    settings = Settings.from_env({"PMR_DATA_DIR": str(tmp_path), "PMR_NO_LLM": "1", "PMR_EMBEDDED_WORKER": "1"})
    app = create_app(settings)
    client = app.test_client()
    assert client.get("/health").status_code == 200
    app.extensions["pmr_worker_stop"].set()
    app.extensions["pmr_worker"].join(timeout=5)
    r = client.get("/health")
    assert r.status_code == 503
    assert r.get_json() == {"ok": False, "worker": "stopped"}


def test_postgres_connections_time_out_instead_of_hanging():
    args = Database.connect_args("postgresql+psycopg://u:p@db.example:5432/pmr")
    assert args["connect_timeout"] > 0
    assert args["keepalives"] == 1
    assert Database.connect_args("sqlite:///x.db") == {"check_same_thread": False}
