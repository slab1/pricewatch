"""Alert-path tests: no network, webhook monkeypatched, temp DB per test."""
import json
import os
import sys
import tempfile

_TMPDIR = tempfile.mkdtemp(prefix="pw_alerts_")
os.environ["PRICEWATCH_DB"] = os.path.join(_TMPDIR, "test.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import alerts  # noqa: E402
import collect  # noqa: E402
import db  # noqa: E402
import pytest  # noqa: E402

db.DB_PATH = os.environ["PRICEWATCH_DB"]


@pytest.fixture
def posts(monkeypatch):
    """Capture every _post call instead of hitting the network."""
    sent = []
    monkeypatch.setattr(alerts, "_post", lambda url, payload: sent.append((url, payload)))
    return sent


@pytest.fixture
def fresh_db(monkeypatch):
    path = os.path.join(tempfile.mkdtemp(prefix="pw_alerts_db_"), "t.db")
    monkeypatch.setattr(db, "DB_PATH", path)


def test_notify_without_webhook_is_quiet(monkeypatch, capsys):
    monkeypatch.delenv("PRICEWATCH_ALERT_WEBHOOK", raising=False)
    alerts.notify("rice", "x", "boom")  # must not raise
    out = capsys.readouterr().out
    assert "ALERT [failure] rice @ x: boom" in out


def test_notify_posts_json_to_webhook(monkeypatch, posts):
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")
    alerts.notify("rice", "nbs", "layout gone", kind="empty_layout")
    assert len(posts) == 1
    url, payload = posts[0]
    assert url == "https://hooks.example/x"
    assert payload["item"] == "rice"
    assert payload["source"] == "nbs"
    assert payload["reason"] == "layout gone"
    assert payload["kind"] == "empty_layout"
    assert payload["service"] == "pricewatch"
    assert "T" in payload["time"]
    json.dumps(payload)  # must be serializable


def test_notify_never_raises_when_webhook_fails(monkeypatch, capsys):
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")

    def boom(url, payload):
        raise OSError("connection refused")

    monkeypatch.setattr(alerts, "_post", boom)
    alerts.notify("rice", "nbs", "x")  # must not raise
    assert "ALERT-SEND-FAILED" in capsys.readouterr().out


def test_run_summary_posts_counts(monkeypatch, posts):
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")
    alerts.notify_run_summary(ok=9, failed=1)
    assert len(posts) == 1
    _, payload = posts[0]
    assert payload["kind"] == "run_summary"
    assert payload["ok"] == 9
    assert payload["failed"] == 1


def test_collect_failure_logs_and_alerts(monkeypatch, posts, fresh_db, tmp_path):
    """A failing enabled source: failure row + notify + run_summary + exit code 1."""
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps([
        {"item": "broken", "source": "s1", "unit": "u", "type": "json_api",
         "url": "http://127.0.0.1:9/x", "path": ["a"], "enabled": True},
    ]))
    failed = collect.run(config_path=cfg)
    assert failed == 1
    with db.connect() as c:
        rows = c.execute("SELECT item, source, reason FROM failures").fetchall()
    assert len(rows) == 1 and rows[0]["item"] == "broken"
    kinds = [p["kind"] for _, p in posts]
    assert "failure" in kinds
    assert "run_summary" in kinds


def test_collect_empty_layout_labeled(monkeypatch, posts, fresh_db, tmp_path):
    """Selector-matched-nothing must be labeled empty_layout, not generic failure."""
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps([
        {"item": "petrol", "source": "s1", "unit": "u", "type": "html_css",
         "url": "http://x", "selector": ".price", "enabled": True},
    ]))
    collect.run(config_path=cfg, html_overrides={"petrol": "<html><body>no prices here</body></html>"})
    payload = next(p for _, p in posts if p["kind"] in ("failure", "empty_layout"))
    assert payload["kind"] == "empty_layout"


def test_collect_all_ok_no_summary(monkeypatch, posts, fresh_db, tmp_path):
    monkeypatch.setenv("PRICEWATCH_ALERT_WEBHOOK", "https://hooks.example/x")
    cfg = tmp_path / "sources.json"
    cfg.write_text(json.dumps([
        {"item": "usd", "source": "s1", "unit": "u", "type": "html_css",
         "url": "http://x", "selector": ".price", "min": 1, "max": 10000, "enabled": True},
    ]))
    failed = collect.run(config_path=cfg, html_overrides={"usd": "<div class='price'>₦1,331</div>"})
    assert failed == 0
    assert posts == []  # no alert at all on a clean run
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM prices").fetchone()[0] == 1
