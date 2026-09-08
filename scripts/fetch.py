#!/usr/bin/env python3
"""Mirror AI-agent news sources into JSON files that a sandboxed agent can read
over raw.githubusercontent.com.

Only stdlib, so the workflow needs no pip install. Each source is merged into a
rolling archive keyed by URL, so a weekly reader still sees everything even
though feeds only carry the last N items.
"""

import json
import pathlib
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

OUT = pathlib.Path(__file__).resolve().parent.parent / "feeds"
RETAIN_DAYS = 60
UA = "ai-agent-weekly-mirror/1.0 (+https://github.com)"

FEEDS = [
    ("simonwillison", "https://simonwillison.net/atom/everything/"),
    ("latentspace", "https://www.latent.space/feed"),
    ("interconnects", "https://www.interconnects.ai/feed"),
]

HN_QUERIES = ["AI agent", "LLM", "tool calling", "evals", "context engineering"]
HN_MIN_POINTS = 60
HN_WINDOW_DAYS = 10

NS = {"atom": "http://www.w3.org/2005/Atom"}


def get(url, timeout=45):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def strip_html(s, limit=6000):
    if not s:
        return ""
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", s)
    s = re.sub(r"(?s)<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<")
          .replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'"))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def parse_date(raw):
    if not raw:
        return None
    raw = raw.strip()
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            d = datetime.strptime(raw.replace("Z", "+0000"), fmt)
            return d.astimezone(timezone.utc) if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            continue
    return None


def text_of(el):
    return el.text if el is not None and el.text else ""


def parse_feed(xml_bytes):
    """Handle both Atom and RSS 2.0."""
    root = ET.fromstring(xml_bytes)
    items = []

    # Atom
    for e in root.findall("atom:entry", NS):
        link = ""
        for l in e.findall("atom:link", NS):
            if l.get("rel") in (None, "alternate"):
                link = l.get("href") or ""
                break
        body = text_of(e.find("atom:content", NS)) or text_of(e.find("atom:summary", NS))
        items.append({
            "title": strip_html(text_of(e.find("atom:title", NS)), 400),
            "url": link,
            "published": text_of(e.find("atom:published", NS)) or text_of(e.find("atom:updated", NS)),
            "content": strip_html(body),
        })

    # RSS
    for e in root.findall("./channel/item"):
        body = ""
        for tag in ("{http://purl.org/rss/1.0/modules/content/}encoded", "description"):
            node = e.find(tag)
            if node is not None and node.text:
                body = node.text
                break
        items.append({
            "title": strip_html(text_of(e.find("title")), 400),
            "url": text_of(e.find("link")),
            "published": text_of(e.find("pubDate")),
            "content": strip_html(body),
        })

    out = []
    for it in items:
        if not it["url"]:
            continue
        d = parse_date(it["published"])
        it["published_iso"] = d.isoformat() if d else ""
        out.append(it)
    return out


def fetch_hn():
    since = int(time.time()) - HN_WINDOW_DAYS * 86400
    seen, items = {}, []
    for q in HN_QUERIES:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={urllib.parse.quote(q)}&tags=story"
            f"&numericFilters=created_at_i%3E{since},points%3E{HN_MIN_POINTS}"
            "&hitsPerPage=50"
        )
        data = json.loads(get(url))
        for h in data.get("hits", []):
            oid = h.get("objectID")
            if not oid or oid in seen:
                continue
            seen[oid] = True
            items.append({
                "title": h.get("title") or "",
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={oid}",
                "hn_url": f"https://news.ycombinator.com/item?id={oid}",
                "points": h.get("points", 0),
                "num_comments": h.get("num_comments", 0),
                "published_iso": (h.get("created_at") or "").replace("Z", "+00:00"),
                "content": "",
                "matched_query": q,
            })
    items.sort(key=lambda x: x["points"], reverse=True)
    return items


def merge(name, fresh):
    """Merge into rolling archive, dedupe by url, drop items older than RETAIN_DAYS."""
    path = OUT / f"{name}.json"
    old = []
    if path.exists():
        try:
            old = json.loads(path.read_text()).get("items", [])
        except Exception:
            old = []

    by_url = {i["url"]: i for i in old if i.get("url")}
    added = 0
    for it in fresh:
        if it["url"] not in by_url:
            added += 1
        by_url[it["url"]] = it

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETAIN_DAYS)
    kept = []
    for it in by_url.values():
        d = parse_date(it.get("published_iso"))
        if d is None or d >= cutoff:
            kept.append(it)
    kept.sort(key=lambda x: x.get("published_iso") or "", reverse=True)

    path.write_text(json.dumps(
        {"source": name, "fetched_at": datetime.now(timezone.utc).isoformat(),
         "count": len(kept), "items": kept},
        ensure_ascii=False, indent=1))
    return len(kept), added


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    status = {}

    for name, url in FEEDS:
        try:
            items = parse_feed(get(url))
            total, added = merge(name, items)
            status[name] = {"ok": True, "fetched": len(items), "archived": total, "new": added}
            print(f"[ok]   {name}: fetched {len(items)}, +{added} new, {total} archived")
        except Exception as e:
            status[name] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            print(f"[FAIL] {name}: {type(e).__name__}: {e}", file=sys.stderr)

    try:
        items = fetch_hn()
        total, added = merge("hackernews", items)
        status["hackernews"] = {"ok": True, "fetched": len(items), "archived": total, "new": added}
        print(f"[ok]   hackernews: fetched {len(items)}, +{added} new, {total} archived")
    except Exception as e:
        status["hackernews"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        print(f"[FAIL] hackernews: {type(e).__name__}: {e}", file=sys.stderr)

    # Pre-filtered 7-day digest: the weekly reader loads only this.
    window = datetime.now(timezone.utc) - timedelta(days=7)
    digest = []
    for name in [f[0] for f in FEEDS] + ["hackernews"]:
        fp = OUT / f"{name}.json"
        if not fp.exists():
            continue
        for it in json.loads(fp.read_text()).get("items", []):
            d = parse_date(it.get("published_iso"))
            if d and d >= window:
                digest.append({**it, "source": name})
    digest.sort(key=lambda x: x.get("published_iso") or "", reverse=True)
    (OUT / "last7days.json").write_text(json.dumps(
        {"generated_at": datetime.now(timezone.utc).isoformat(),
         "window_start": window.isoformat(),
         "count": len(digest), "items": digest},
        ensure_ascii=False, indent=1))
    print(f"[ok]   last7days digest: {len(digest)} items")

    (OUT / "index.json").write_text(json.dumps(
        {"updated_at": datetime.now(timezone.utc).isoformat(),
         "retain_days": RETAIN_DAYS, "sources": status},
        ensure_ascii=False, indent=1))

    failed = [k for k, v in status.items() if not v["ok"]]
    if len(failed) == len(status):
        print("all sources failed", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
