"""Run once a day (cron). Fetches every enabled source, stores the reading,
logs failures, and exits non-zero if anything broke so cron/monitors notice."""
import json
import sys
import time
from pathlib import Path

import alerts
import db
import requests
from requests.exceptions import RequestException
from sources import FETCHERS

CONFIG = Path(__file__).with_name("sources.json")

# fetcher messages that mean "the page is there but the value is not"
# (i.e. a layout change), as opposed to network/HTTP errors
EMPTY_LAYOUT_MARKERS = ("matched nothing", "no number found")


def run(config_path=CONFIG, html_overrides=None):
    html_overrides = html_overrides or {}
    entries = json.loads(Path(config_path).read_text())
    ok, failed = 0, 0
    for cfg in entries:
        if not cfg.get("enabled"):
            continue
        name = f"{cfg['item']} @ {cfg['source']}"
        # Retry logic for transient errors (e.g., network issues)
        max_retries = 3
        base_delay = 1  # second
        last_exception = None
        for attempt in range(max_retries):
            try:
                value = FETCHERS[cfg["type"]](cfg, html_overrides.get(cfg["item"]))
                break   # success, break out of retry loop
            except RequestException as e:
                last_exception = e
                if attempt == max_retries - 1:
                    # No more retries left
                    break
                delay = base_delay * (2 ** attempt)  # exponential backoff
                time.sleep(delay)
            except Exception as e:
                # For non-RequestException, we don't retry
                last_exception = e
                break
        else:
            # This else runs if the loop didn't break, meaning we exhausted retries
            # (only for RequestException case where we break only on success or after last attempt)
            # Actually, the above break on success will skip this else.
            # We need to handle the case where we break due to last exception after retries.
            # Let's restructure: we'll use a flag.
            pass

        # If we have a last_exception, then all retries failed or we had a non-retriable exception
        if 'last_exception' in locals() and last_exception is not None:
            e = last_exception
            db.log_failure(cfg["item"], cfg["source"], str(e)[:300])
            kind = "empty_layout" if any(m in str(e) for m in EMPTY_LAYOUT_MARKERS) else "failure"
            alerts.notify(cfg["item"], cfg["source"], str(e)[:300], kind=kind)
            print(f"FAIL  {name}: {e}", file=sys.stderr)
            failed += 1
        else:
            # Success case (we broke out of the retry loop with a value)
            db.save_price(cfg["item"], cfg["source"], value, cfg["unit"])
            print(f"OK    {name} = {value}")
            ok += 1

    if failed:
        alerts.notify_run_summary(ok, failed)
    print(f"done: {ok} ok, {failed} failed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if run() else 0)
