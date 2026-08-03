#!/usr/bin/env python3
"""
Archive.ph News Scraper — REST API
===================================
FastAPI application that exposes the scraper as HTTP endpoints.

Endpoints:
  GET  /health               Health check (Coolify / Docker probe)
  GET  /                     API info & usage
  POST /archive-submit       Submit a URL to archive.ph and get back the snapshot URL
  POST /scrape               Scrape a news article via archive.ph

Recommended n8n workflow:
  1. POST /archive-submit  →  get archive_url  (e.g. https://archive.ph/H6GcX)
  2. POST /scrape          →  pass archive_url  →  get full article JSON

Optional authentication:
  Set the API_KEY environment variable to require an X-Api-Key header.
  If API_KEY is not set, the API is open (no auth).

Environment variables:
  API_KEY   (optional) — shared secret for X-Api-Key header auth
  PORT      (optional) — override default port 8000 (used by uvicorn CLI only)
"""

import os
import logging
from typing import Optional

from fastapi import FastAPI, HTTPException, Security, status
from fastapi.security.api_key import APIKeyHeader
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from scraper import scrape_news, submit_to_archive

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_API_KEY = os.getenv("API_KEY", "")  # empty = no auth required

api_key_header = APIKeyHeader(name="X-Api-Key", auto_error=False)


def verify_api_key(key: Optional[str] = Security(api_key_header)) -> None:
    """Dependency: validate X-Api-Key if API_KEY env var is configured."""
    if not _API_KEY:
        return  # auth disabled
    if key != _API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Api-Key header.",
        )


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Archive.ph News Scraper",
    description=(
        "Submit any news URL to archive.ph, then scrape the archived article "
        "for structured metadata — title, author, date, content, and more."
    ),
    version="1.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow all origins so n8n (or any client) can call it freely
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Shared URL validator
# ---------------------------------------------------------------------------

def _validate_url(v: str) -> str:
    v = v.strip()
    if not v:
        raise ValueError("url must not be empty")
    if not v.startswith(("http://", "https://")):
        raise ValueError("url must start with http:// or https://")
    return v


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class ArchiveSubmitRequest(BaseModel):
    """Body for POST /archive-submit."""
    url: str
    force_new: bool = False

    @field_validator("url")
    @classmethod
    def url_must_be_valid(cls, v: str) -> str:
        return _validate_url(v)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "url": "https://www.ft.com/content/e9027253-e13c-460a-a4b1-f9047e5a6ca7",
                    "force_new": False,
                }
            ]
        }
    }


class ArchiveSubmitResponse(BaseModel):
    archive_url: Optional[str] = None
    original_url: str
    status: str                    # "archived" | "processing" | "error"
    submitted_at: str
    error: Optional[str] = None
    hint: Optional[str] = None


class ScrapeRequest(BaseModel):
    """Body for POST /scrape."""
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_valid(cls, v: str) -> str:
        return _validate_url(v)

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"url": "https://archive.ph/H6GcX"},
                {"url": "https://www.bbc.com/news/articles/abc123"},
            ]
        }
    }


class ScrapeResponse(BaseModel):
    title: Optional[str] = None
    author: Optional[str] = None
    published_date: Optional[str] = None
    description: Optional[str] = None
    content: Optional[str] = None
    origin_site: Optional[str] = None
    original_url: str
    archive_url: Optional[str] = None
    snapshot_date: Optional[str] = None
    total_snapshots_available: Optional[int] = None
    scraped_at: str
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get(
    "/health",
    summary="Health check",
    description="Returns 200 OK when the service is running. Used by Coolify and Docker health probes.",
    tags=["System"],
)
def health_check():
    return {"status": "ok"}


@app.get(
    "/",
    summary="API info",
    description="Returns usage information and curl examples for all endpoints.",
    tags=["System"],
)
def root():
    return {
        "service": "Archive.ph News Scraper",
        "version": "1.1.0",
        "auth_required": bool(_API_KEY),
        "workflow": [
            "1. POST /archive-submit  ->  submits URL to archive.ph, returns archive_url",
            "2. POST /scrape          ->  pass the archive_url, returns full article JSON",
        ],
        "endpoints": {
            "POST /archive-submit": "Submit a news URL to archive.ph -> get snapshot URL",
            "POST /scrape":         "Scrape an article from archive.ph -> get full JSON",
            "GET  /health":         "Health check",
            "GET  /docs":           "Interactive Swagger UI",
            "GET  /redoc":          "ReDoc documentation",
        },
        "curl_examples": {
            "step_1_archive": (
                'curl -X POST https://your-domain.com/archive-submit '
                '-H "Content-Type: application/json" '
                '-H "X-Api-Key: YOUR_KEY" '
                '-d \'{"url": "https://www.ft.com/content/abc123", "force_new": false}\''
            ),
            "step_2_scrape": (
                'curl -X POST https://your-domain.com/scrape '
                '-H "Content-Type: application/json" '
                '-H "X-Api-Key: YOUR_KEY" '
                '-d \'{"url": "https://archive.ph/H6GcX"}\''
            ),
        },
    }


@app.post(
    "/archive-submit",
    response_model=ArchiveSubmitResponse,
    summary="Submit URL to archive.ph",
    description=(
        "Submits a news article URL to **archive.ph** for archiving. "
        "Waits up to 120 seconds for archive.ph to finish processing, then "
        "returns the canonical snapshot URL (e.g. `https://archive.ph/H6GcX`). "
        "\n\n"
        "Set `force_new: true` to always create a fresh snapshot even when archive.ph "
        "already has a recent one. Defaults to `false` which reuses an existing snapshot "
        "if available (faster).\n\n"
        "**Typical n8n flow**: call this first, take `archive_url`, then call `POST /scrape`."
    ),
    tags=["Archiver"],
    dependencies=[Security(verify_api_key)],
)
def archive_submit_endpoint(body: ArchiveSubmitRequest):
    log.info("Submitting to archive.ph: %s (force_new=%s)", body.url, body.force_new)
    try:
        result = submit_to_archive(url=body.url, force_new=body.force_new)
    except Exception as exc:
        log.exception("Archive submission error for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Archive submission error: {exc}",
        )

    if result.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=result.get("error", "Unknown archive.ph error"),
        )

    log.info(
        "Archived: %s -> %s (status=%s)",
        body.url, result.get("archive_url"), result.get("status"),
    )
    return result


@app.post(
    "/scrape",
    response_model=ScrapeResponse,
    summary="Scrape an archived article",
    description=(
        "Accepts either an **original news URL** or a direct **archive.ph snapshot URL** "
        "(e.g. `https://archive.ph/H6GcX`). "
        "Finds the most recent snapshot and returns all available article metadata: "
        "title, author, published date, full content, origin site, and URLs."
        "\n\n"
        "**Tip**: pass the `archive_url` returned by `POST /archive-submit` directly here."
    ),
    tags=["Scraper"],
    dependencies=[Security(verify_api_key)],
)
def scrape_endpoint(body: ScrapeRequest):
    log.info("Scraping: %s", body.url)
    try:
        result = scrape_news(body.url)
    except Exception as exc:
        log.exception("Scraper error for %s: %s", body.url, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Scraper error: {exc}",
        )

    # If the scraper itself returned an error dict, surface it as 404
    if "error" in result and result.get("archive_url") is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result["error"],
        )

    log.info("Done: %s -- archive: %s", body.url, result.get("archive_url"))
    return result
