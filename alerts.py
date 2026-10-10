"""Push alerts when a scraper fails or returns empty (layout changed).

Set PRICEWATCH_ALERT_WEBHOOK to a URL (Slack incoming webhook, healthchecks.io,
Discord, or your own endpoint). Every source failure POSTs one JSON object; a
run with failures also gets a summary. Alerts must never break collection:
everything here is wrapped so a bad webhook cannot raise into collect.py.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

TIMEOUT = 10


def _post(url, payload):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "User-Agent": "pricewatch-alerts/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT):
        pass


def notify(item, source, reason, kind="failure"):
    """kind: 'failure' (fetch/parse error) or 'empty_layout' (selector matched nothing)."""
    url = os.environ.get("PRICEWATCH_ALERT_WEBHOOK")
    message = f"PriceWatch alert: {kind} for {item} from {source}: {reason}"
    payload = {
        "service": "pricewatch",
        "item": item,
        "source": source,
        "reason": reason,
        "kind": kind,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text": message,
        "content": message,
    }
    if not url:
        print(f"ALERT [{kind}] {item} @ {source}: {reason}")
        return
    try:
        _post(url, payload)
    except Exception as e:  # alerting must never break the run
        print(f"ALERT-SEND-FAILED [{kind}] {item} @ {source}: {e}")


def notify_run_summary(ok, failed):
    """Called once per collect run when at least one source failed."""
    url = os.environ.get("PRICEWATCH_ALERT_WEBHOOK")
    message = f"PriceWatch run summary: {ok} ok, {failed} failed"
    payload = {
        "service": "pricewatch",
        "item": "*",
        "source": "*",
        "reason": f"collect run: {ok} ok, {failed} failed",
        "kind": "run_summary",
        "ok": ok,
        "failed": failed,
        "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "text": message,
        "content": message,
    }
    if not url:
        print(f"ALERT [run_summary] {ok} ok, {failed} failed")
        return
    try:
        _post(url, payload)
    except Exception as e:
        print(f"ALERT-SEND-FAILED [run_summary]: {e}")
