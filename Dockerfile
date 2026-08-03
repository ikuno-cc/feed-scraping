# ─────────────────────────────────────────────────────────────────────────────
# Archive.ph News Scraper — Dockerfile
# ─────────────────────────────────────────────────────────────────────────────
# Build:   docker build -t news-scraper .
# Run:     docker run -p 8000:8000 -e API_KEY=mysecret news-scraper
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# --- System dependencies ---
# curl_cffi ships its own libcurl binary, so no extra system curl needed.
# We only need build-essential for cffi compilation.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# --- App directory & non-root user ---
WORKDIR /app

RUN groupadd --gid 1001 appgroup \
    && useradd --uid 1001 --gid appgroup --shell /bin/bash --no-create-home appuser

# --- Python dependencies (cached layer) ---
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# --- Application code ---
COPY scraper.py .
COPY main.py .

# Switch to non-root user
RUN chown -R appuser:appgroup /app
USER appuser

# --- Runtime config ---
ENV PORT=8000
EXPOSE 8000

# Health check for Coolify / Docker Compose
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# --- Entrypoint ---
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--log-level", "info"]
