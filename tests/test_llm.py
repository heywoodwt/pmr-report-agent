"""The Claude client: key lookup order, cache-first behavior and usage counting. No live calls are made here."""

import json
from types import SimpleNamespace

from pmr import llm

# A tiny schema shared by the tests below.
SCHEMA = {"type": "object", "properties": {"word": {"type": "string"}}, "required": ["word"],
          "additionalProperties": False}


def test_environment_key_wins_over_dotenv(tmp_path, monkeypatch):
    # Both sources present: the environment variable is used.
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=from-file\n", encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "from-env")
    assert llm.api_key(tmp_path / ".env") == "from-env"


def test_dotenv_is_read_without_touching_the_environment(tmp_path, monkeypatch):
    # Only the file has the key: it is returned, and the environment stays unset.
    (tmp_path / ".env").write_text('# comment\nOTHER=1\nANTHROPIC_API_KEY="from-file"\n', encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.api_key(tmp_path / ".env") == "from-file"
    import os
    assert "ANTHROPIC_API_KEY" not in os.environ


def test_no_key_anywhere_returns_none(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert llm.api_key(tmp_path / "missing.env") is None


def test_cache_only_mode_returns_none_without_calling(tmp_path):
    # enabled=False never reads a key and never calls the API.
    c = llm.Claude(cache_dir=tmp_path, enabled=False)
    assert c.json("task", [{"type": "text", "text": "x"}], SCHEMA) is None
    assert c.calls == {"cached": 0, "live": 0, "unavailable": 1, "failed": 0}


def test_cached_answer_is_used_even_without_a_key(tmp_path, monkeypatch):
    # First, a "live" answer is faked by stubbing the network call.
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    c = llm.Claude(cache_dir=tmp_path)
    monkeypatch.setattr(c, "_call", lambda *a: ({"word": "OK"}, llm.MODEL, {}))
    assert c.json("task", [{"type": "text", "text": "x"}], SCHEMA) == {"word": "OK"}
    # A second client in cache-only mode gets the same answer from disk.
    again = llm.Claude(cache_dir=tmp_path, enabled=False)
    assert again.json("task", [{"type": "text", "text": "x"}], SCHEMA) == {"word": "OK"}
    assert again.calls["cached"] == 1
    # The cache file records the model and the answer, keyed by the request hash.
    files = list(tmp_path.glob("*.json"))
    assert len(files) == 1 and json.loads(files[0].read_text())["model"] == llm.MODEL


def test_live_usage_is_counted_priced_and_cached(tmp_path, monkeypatch):
    # A stand-in for the SDK client: one JSON reply with known token counts.
    reply = SimpleNamespace(stop_reason="end_turn", model=llm.MODEL,
                            usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000),
                            content=[SimpleNamespace(type="text", text='{"word": "OK"}')])
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    c = llm.Claude(cache_dir=tmp_path)
    c._client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: reply)))
    assert c.json("task", [{"type": "text", "text": "x"}], SCHEMA) == {"word": "OK"}
    # Run totals: 1M input at $4 plus 0.1M output at $20 is $6.
    assert (c.usage["input_tokens"], c.usage["output_tokens"]) == (1_000_000, 100_000)
    assert c.cost_usd == 6.0
    # The cache file records the call's usage; replaying it from cache spends nothing.
    stored = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert stored["usage"]["output_tokens"] == 100_000
    again = llm.Claude(cache_dir=tmp_path, enabled=False)
    again.json("task", [{"type": "text", "text": "x"}], SCHEMA)
    assert again.cost_usd == 0
