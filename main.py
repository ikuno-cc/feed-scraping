#!/usr/bin/env python3
"""
Archive.ph News Scraper — REST API
===================================
FastAPI application that exposes the scraper as HTTP endpoints.

Endpoints:
  GET  /health          Health check (Coolify / Docker probe)
  GET  /                API info & usage
  POST /scrape          Scrape a news article via archive.ph

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
from fastapi.responses import JSONResponse
from pydantic import BaseModel, HttpUrl, field_validator

from scraper import scrape_news

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
        "Given any news article URL, searches archive.ph for all snapshots, "
        "picks the most recent one, and returns structured article metadata as JSON."
    ),
    version="1.0.0",
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
# Request / Response models
# ---------------------------------------------------------------------------

class ScrapeRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def url_must_be_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("url must not be empty")
        if not v.startswith(("http://", "https://")):
            raise ValueError("url must start with http:// or https://")
        return v

    model_config = {
        "json_schema_extra": {
            "examples": [
                {"url": "https://www.bbc.com/news/articles/abc123"},
                {"url": "https://www.reuters.com/world/some-story-2024-01-15/"},
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
    description="Returns basic usage information about the API.",
    tags=["System"],
)
def root():
    return {
        "service": "Archive.ph News Scraper",
        "version": "1.0.0",
        "auth_required": bool(_API_KEY),
        "endpoints": {
            "POST /scrape": "Scrape a news article from archive.ph",
            "GET  /health": "Health check",
            "GET  /docs":   "Interactive Swagger UI",
            "GET  /redoc":  "ReDoc documentation",
        },
        "curl_example": (
            'curl -X POST https://your-domain.com/scrape '
            '-H "Content-Type: application/json" '
            '-H "X-Api-Key: YOUR_KEY" '
            '-d \'{"url": "https://www.bbc.com/news/articles/abc123"}\''
        ),
    }


@app.post(
    "/scrape",
    response_model=ScrapeResponse,
    summary="Scrape a news article",
    description=(
        "Accepts a news article URL, searches archive.ph for all archived snapshots, "
        "automatically selects the **most recent** snapshot, and returns all available "
        "article metadata: title, author, published date, content, origin site, and URLs."
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

    log.info("Done: %s — archive: %s", body.url, result.get("archive_url"))
    return result
