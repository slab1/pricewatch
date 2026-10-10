"""Source fetchers. Each returns a float or raises an error.

Two kinds:
  - "json_api": read a number from a JSON endpoint by key path
  - "html_css": read a number from a web page using a CSS selector
New sources need only a new entry in sources.json, no code changes."""
import os
import re
import requests
from bs4 import BeautifulSoup

HEADERS = {"User-Agent": "pricewatch/0.1 (+price research; contact: set-your-email)"}
TIMEOUT = 20


def parse_number(text):
    """'₦1,234.50 per litre' -> 1234.5"""
    cleaned = text.replace(",", "")
    m = re.search(r"\d+(?:\.\d+)?", cleaned)
    if not m:
        raise ValueError(f"no number found in {text!r}")
    return float(m.group())


def check_range(value, cfg):
    """Reject values outside the optional min/max so a layout or API change can't store garbage."""
    if ("min" in cfg and value < cfg["min"]) or ("max" in cfg and value > cfg["max"]):
        raise ValueError(f"value {value} outside sanity range [{cfg.get('min')}, {cfg.get('max')}]")
    return value


def build_headers(cfg):
    """Optional per-source headers. Values may reference env vars, e.g.
    {"Authorization": "Bearer ${ALLRATES_KEY}"}, so keys never sit in sources.json."""
    headers = dict(HEADERS)
    for name, value in cfg.get("headers", {}).items():
        expanded = os.path.expandvars(value)
        if "$" in expanded:
            raise ValueError(f"header {name!r} needs an environment variable that is not set: {value}")
        headers[name] = expanded
    return headers


def fetch_json_api(cfg, html_override=None):
    r = requests.get(cfg["url"], headers=build_headers(cfg), timeout=TIMEOUT)
    r.raise_for_status()
    node = r.json()
    for key in cfg["path"]:
        node = node[key]
    return check_range(float(node), cfg)


def fetch_html_css(cfg, html_override=None):
    if html_override is not None:
        html = html_override
    else:
        r = requests.get(cfg["url"], headers=HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        html = r.text
    el = BeautifulSoup(html, "html.parser").select_one(cfg["selector"])
    if el is None:
        raise ValueError(f"selector {cfg['selector']!r} matched nothing (page layout changed?)")
    return check_range(parse_number(el.get_text(" ", strip=True)), cfg)


FETCHERS = {"json_api": fetch_json_api, "html_css": fetch_html_css}
