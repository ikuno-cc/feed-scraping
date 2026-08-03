#!/usr/bin/env python3
"""
Archive.ph News Scraper
=======================
Given any news article URL, this scraper:
  1. Searches archive.ph for all archived snapshots of that URL
  2. Automatically selects the most recent snapshot
  3. Scrapes the archived article for all available metadata
  4. Returns a structured JSON result

Usage:
    python scraper.py <url>
    python scraper.py "https://www.bbc.com/news/articles/abc123"

Requirements:
    pip install -r requirements.txt
"""

import sys
import json
import re
import argparse
from datetime import datetime, timezone
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARCHIVE_BASE = "https://archive.ph"

# Mimic a real Chrome browser for TLS fingerprint and headers
BROWSER_IMPERSONATE = "chrome120"

# Snapshot short-ID pattern used by archive.ph (4–10 alphanumeric chars)
SNAPSHOT_ID_RE = re.compile(r"^/([A-Za-z0-9]{4,12})$")

# Common article content container class fragments (ordered by priority)
CONTENT_CLASS_HINTS = [
    "article-body",
    "article-content",
    "article__body",
    "article__content",
    "story-body",
    "story-content",
    "post-body",
    "post-content",
    "entry-content",
    "main-content",
    "body-text",
    "text-body",
    "articleBody",
    "article_body",
    "content-body",
]

# Tags that never contain useful article content
JUNK_TAGS = [
    "script", "style", "nav", "header", "footer",
    "aside", "noscript", "figure", "figcaption",
    "form", "button", "iframe",
]


# ---------------------------------------------------------------------------
# HTTP Session
# ---------------------------------------------------------------------------

def _make_session() -> cffi_requests.Session:
    """Return a curl_cffi Session that impersonates a real Chrome browser."""
    session = cffi_requests.Session(impersonate=BROWSER_IMPERSONATE)
    session.headers.update({
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Referer": "https://archive.ph/",
    })
    return session


# ---------------------------------------------------------------------------
# Step 1 — Search archive.ph for snapshots
# ---------------------------------------------------------------------------

def search_snapshots(url: str, session: cffi_requests.Session) -> list[dict]:
    """
    Fetch the archive.ph listing page for *url* and return a list of snapshot dicts
    sorted most-recent-first, each with keys: 'snapshot_url', 'date_text'.

    If archive.ph redirects directly to a single snapshot, that snapshot is
    returned as a one-element list.
    """
    search_url = f"{ARCHIVE_BASE}/{url}"

    resp = session.get(search_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    final_url: str = resp.url  # may have been redirected

    # --- Case A: redirected directly to a snapshot page ---
    # archive.ph redirects to the most-recent snapshot when only one exists
    # or when the URL is already a known archive link.
    if _is_snapshot_url(final_url) and final_url.rstrip("/") != search_url.rstrip("/"):
        return [{"snapshot_url": final_url, "date_text": "latest (redirected)"}]

    soup = BeautifulSoup(resp.content, "lxml")
    snapshots: list[dict] = []

    # --- Case B: listing page with multiple snapshots ---
    # archive.ph renders each snapshot inside a <div class="TEXT-BLOCK"> or
    # <div class="THUMBS-BLOCK">.  The anchor inside contains the short ID.
    # We also do a broader fallback scan for any anchor whose href is a short ID.

    seen_ids: set[str] = set()

    # Primary: structured blocks
    for block in soup.find_all("div", class_=lambda c: c and ("THUMBS" in c or "TEXT" in c)):
        for a in block.find_all("a", href=True):
            match = SNAPSHOT_ID_RE.match(a["href"])
            if match and match.group(1) not in seen_ids:
                snap_id = match.group(1)
                seen_ids.add(snap_id)
                snapshots.append({
                    "snapshot_url": f"{ARCHIVE_BASE}/{snap_id}",
                    "date_text": block.get_text(" ", strip=True),
                })

    # Fallback: scan every anchor on the page
    if not snapshots:
        for a in soup.find_all("a", href=True):
            match = SNAPSHOT_ID_RE.match(a["href"])
            if match and match.group(1) not in seen_ids:
                snap_id = match.group(1)
                seen_ids.add(snap_id)
                date_text = a.get_text(strip=True)
                # Skip purely navigational links (empty text or icons)
                if len(date_text) < 4:
                    continue
                snapshots.append({
                    "snapshot_url": f"{ARCHIVE_BASE}/{snap_id}",
                    "date_text": date_text,
                })

    # archive.ph lists most-recent first, so we preserve order
    return snapshots


def _is_snapshot_url(url: str) -> bool:
    """Return True if *url* looks like an archive.ph snapshot URL."""
    parsed = urlparse(url)
    if parsed.netloc not in ("archive.ph", "archive.today", "archive.is",
                             "archive.li", "archive.fo", "archive.vn",
                             "archive.md"):
        return False
    return bool(SNAPSHOT_ID_RE.match(parsed.path))


# ---------------------------------------------------------------------------
# Step 2 — Scrape the archived article
# ---------------------------------------------------------------------------

def scrape_snapshot(
    snapshot_url: str,
    original_url: str,
    session: cffi_requests.Session,
) -> dict:
    """
    Fetch *snapshot_url* from archive.ph and extract all article metadata.
    Returns a dict with: title, author, published_date, description,
    content, origin_site, original_url, archive_url, scraped_at.
    """
    resp = session.get(snapshot_url, timeout=30, allow_redirects=True)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.content, "lxml")

    # Remove junk tags before any text extraction
    for tag in soup.find_all(JUNK_TAGS):
        tag.decompose()

    # archive.ph wraps the archived page inside <div id="CONTENT">
    # We want to operate on the *inner* archived page, not archive.ph's chrome.
    content_wrapper = soup.find("div", id="CONTENT") or soup

    return {
        "title":          _extract_title(content_wrapper),
        "author":         _extract_author(content_wrapper),
        "published_date": _extract_date(content_wrapper),
        "description":    _extract_description(content_wrapper),
        "content":        _extract_body(content_wrapper),
        "origin_site":    _origin_site(original_url),
        "original_url":   original_url,
        "archive_url":    snapshot_url,
        "scraped_at":     datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _meta(soup: BeautifulSoup, **attrs) -> str | None:
    """Return content of a <meta> tag matching the given attributes."""
    tag = soup.find("meta", attrs=attrs)
    if tag and tag.get("content"):
        return tag["content"].strip() or None
    return None


def _extract_title(soup: BeautifulSoup) -> str | None:
    candidates = [
        _meta(soup, property="og:title"),
        _meta(soup, name="twitter:title"),
    ]
    # Try JSON-LD
    candidates.append(_jsonld_field(soup, "headline") or _jsonld_field(soup, "name"))
    # h1
    h1 = soup.find("h1")
    if h1:
        candidates.append(h1.get_text(strip=True))
    # <title>
    t = soup.find("title")
    if t:
        candidates.append(t.get_text(strip=True))

    return _first(candidates)


def _extract_author(soup: BeautifulSoup) -> str | None:
    candidates = [
        _meta(soup, property="article:author"),
        _meta(soup, name="author"),
        _meta(soup, name="byl"),
        _meta(soup, name="sailthru.author"),
        _meta(soup, name="DC.creator"),
    ]

    # JSON-LD author
    author_ld = _jsonld_field(soup, "author")
    if isinstance(author_ld, dict):
        candidates.append(author_ld.get("name"))
    elif isinstance(author_ld, list):
        names = [
            a.get("name") for a in author_ld
            if isinstance(a, dict) and a.get("name")
        ]
        candidates.append(", ".join(names) if names else None)
    elif isinstance(author_ld, str):
        candidates.append(author_ld)

    # CSS/attribute selectors (itemprop, rel, class hints)
    for selector in [
        "[itemprop='author']",
        "[rel='author']",
        "[class*='author']",
        "[class*='byline']",
        "[class*='ArticleAuthor']",
        "[class*='Byline']",
    ]:
        try:
            el = soup.select_one(selector)
            if el:
                text = el.get_text(strip=True)
                if text and len(text) <= 120:
                    candidates.append(text)
                    break
        except Exception:
            pass

    return _first(candidates)


def _extract_date(soup: BeautifulSoup) -> str | None:
    candidates = [
        _meta(soup, property="article:published_time"),
        _meta(soup, name="article:published_time"),
        _meta(soup, property="datePublished"),
        _meta(soup, name="date"),
        _meta(soup, name="publishdate"),
        _meta(soup, name="sailthru.date"),
        _meta(soup, name="DC.date"),
    ]

    # JSON-LD
    candidates.append(_jsonld_field(soup, "datePublished"))

    # <time datetime="...">
    time_tag = soup.find("time", attrs={"datetime": True})
    if time_tag:
        candidates.append(time_tag["datetime"])

    # itemprop="datePublished"
    el = soup.find(attrs={"itemprop": "datePublished"})
    if el:
        candidates.append(
            el.get("content") or el.get("datetime") or el.get_text(strip=True)
        )

    return _first(candidates)


def _extract_description(soup: BeautifulSoup) -> str | None:
    candidates = [
        _meta(soup, property="og:description"),
        _meta(soup, name="description"),
        _meta(soup, name="twitter:description"),
        _jsonld_field(soup, "description"),
    ]
    return _first(candidates)


def _extract_body(soup: BeautifulSoup) -> str | None:
    # 1. <article> tag
    article = soup.find("article")
    if article:
        return _clean_text(article.get_text("\n"))

    # 2. itemprop="articleBody"
    body_el = soup.find(attrs={"itemprop": "articleBody"})
    if body_el:
        return _clean_text(body_el.get_text("\n"))

    # 3. Common content container class names
    for hint in CONTENT_CLASS_HINTS:
        el = soup.find(
            True,
            class_=lambda c: c and hint.lower() in " ".join(c).lower() if c else False,
        )
        if el:
            return _clean_text(el.get_text("\n"))

    # 4. Largest <div> by text length (heuristic fallback)
    best_div = None
    best_len = 0
    for div in soup.find_all("div"):
        text = div.get_text()
        if len(text) > best_len:
            best_len = len(text)
            best_div = div
    if best_div:
        return _clean_text(best_div.get_text("\n"))

    # 5. Whole body
    body = soup.find("body")
    if body:
        return _clean_text(body.get_text("\n"))

    return None


# ---------------------------------------------------------------------------
# Micro-utilities
# ---------------------------------------------------------------------------

def _jsonld_field(soup: BeautifulSoup, field: str):
    """Extract a field from the first valid JSON-LD <script> block."""
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            raw = script.string or ""
            data = json.loads(raw)
            # data can be a list or a dict
            if isinstance(data, list):
                for item in data:
                    val = item.get(field) if isinstance(item, dict) else None
                    if val is not None:
                        return val
            elif isinstance(data, dict):
                val = data.get(field)
                if val is not None:
                    return val
        except Exception:
            continue
    return None


def _first(candidates: list) -> str | None:
    """Return the first non-empty string from *candidates*."""
    for c in candidates:
        if c and isinstance(c, str) and c.strip():
            return c.strip()
    return None


def _origin_site(url: str) -> str:
    """Return the bare hostname (no www.) of a URL."""
    try:
        host = urlparse(url).netloc
        return re.sub(r"^www\.", "", host)
    except Exception:
        return url


def _clean_text(text: str) -> str:
    """Collapse blank lines and strip each line."""
    lines = [line.strip() for line in text.splitlines()]
    # Collapse 3+ consecutive blank lines into 2
    result = []
    blank_run = 0
    for line in lines:
        if line == "":
            blank_run += 1
            if blank_run <= 2:
                result.append("")
        else:
            blank_run = 0
            result.append(line)
    return "\n".join(result).strip()


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------

def scrape_news(url: str) -> dict:
    """
    Full pipeline: search archive.ph for *url*, pick the most recent snapshot,
    scrape the article, and return a structured dict.

    This function raises on HTTP errors. The caller is responsible for
    handling exceptions and converting to JSON.
    """
    session = _make_session()

    # ── Step 1: find snapshots ──────────────────────────────────────────────
    snapshots = search_snapshots(url, session)

    if not snapshots:
        return {
            "error": "No snapshots found on archive.ph for this URL.",
            "original_url": url,
            "archive_search_url": f"{ARCHIVE_BASE}/{url}",
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    # ── Step 2: pick most recent (archive.ph lists newest first) ────────────
    most_recent = snapshots[0]

    # ── Step 3: scrape the archived article ─────────────────────────────────
    result = scrape_snapshot(
        snapshot_url=most_recent["snapshot_url"],
        original_url=url,
        session=session,
    )

    # ── Enrich with snapshot metadata ───────────────────────────────────────
    result["snapshot_date"] = most_recent["date_text"]
    result["total_snapshots_available"] = len(snapshots)

    return result


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scrape a news article from archive.ph and return JSON.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scraper.py "https://www.bbc.com/news/articles/abc123"
  python scraper.py "https://www.reuters.com/world/some-article-2024-01-15/"
        """,
    )
    parser.add_argument(
        "url",
        help="The original news article URL to look up on archive.ph",
    )
    parser.add_argument(
        "--indent",
        type=int,
        default=2,
        help="JSON indentation level (default: 2, set 0 for compact)",
    )
    args = parser.parse_args()

    try:
        result = scrape_news(args.url)
        indent = args.indent if args.indent > 0 else None
        print(json.dumps(result, ensure_ascii=False, indent=indent))
    except KeyboardInterrupt:
        print("\nAborted.", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        error_payload = {
            "error": str(exc),
            "original_url": args.url,
            "scraped_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        print(json.dumps(error_payload, ensure_ascii=False, indent=args.indent),
              file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
