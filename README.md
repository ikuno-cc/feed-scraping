# Archive.ph News Scraper

A Python command-line tool that takes any news article URL, searches **archive.ph** for all archived snapshots, automatically picks the **most recent** one, and returns a fully structured **JSON** result.

---

## Features

- 🔍 **Auto-searches** archive.ph for all snapshots of any URL
- 🕐 **Picks the most recent** snapshot automatically (no user interaction)
- 📰 **Extracts all article metadata**: title, author, published date, description, full content, origin site
- 🔗 **Includes both** the original URL and the archive.ph snapshot URL
- 🛡️ **Bypasses Cloudflare** using `curl_cffi` TLS fingerprint impersonation
- 📦 **Pure JSON output** — pipe it to any downstream tool

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
python scraper.py "<news_article_url>"
```

### Examples

```bash
# BBC article
python scraper.py "https://www.bbc.com/news/articles/abc123"

# Reuters article
python scraper.py "https://www.reuters.com/world/some-story-2024-01-15/"

# Compact JSON (no indentation)
python scraper.py "https://example.com/article" --indent 0
```

---

## JSON Output Format

```json
{
  "title": "Article Headline",
  "author": "Jane Doe",
  "published_date": "2024-01-15T10:30:00Z",
  "description": "A short summary of the article...",
  "content": "Full article body text...",
  "origin_site": "bbc.com",
  "original_url": "https://www.bbc.com/news/articles/abc123",
  "archive_url": "https://archive.ph/AbCdE",
  "snapshot_date": "2024.01.15 10:35",
  "total_snapshots_available": 3,
  "scraped_at": "2024-01-20T08:00:00Z"
}
```

### Fields

| Field | Description |
|---|---|
| `title` | Article headline (from meta tags, h1, or title tag) |
| `author` | Author name(s) extracted from meta tags, JSON-LD, or byline elements |
| `published_date` | ISO 8601 publication date from meta tags or JSON-LD |
| `description` | Article summary/description |
| `content` | Full article body text |
| `origin_site` | Bare domain of the original news source |
| `original_url` | The URL you provided as input |
| `archive_url` | The specific archive.ph snapshot URL used |
| `snapshot_date` | Date string shown on archive.ph for this snapshot |
| `total_snapshots_available` | How many total snapshots archive.ph has for this URL |
| `scraped_at` | UTC timestamp of when the scrape was performed |

---

## Error Handling

If something goes wrong (no snapshots found, HTTP error, etc.), the tool returns a JSON error object to **stderr** and exits with code 1:

```json
{
  "error": "Description of what went wrong",
  "original_url": "https://...",
  "scraped_at": "2024-01-20T08:00:00Z"
}
```

---

## Notes

- `curl_cffi` impersonates Chrome's TLS fingerprint, which helps bypass most Cloudflare bot protections on archive.ph.
- archive.ph lists snapshots **most-recent first**, so the scraper always picks index 0.
- If archive.ph only has one snapshot for a URL, it may redirect directly — the scraper handles this automatically.
