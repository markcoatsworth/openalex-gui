#!/usr/bin/env python3
"""
Pre-fetches topic-specific citation counts per institution from OpenAlex
and writes results to public/ai-rankings-cache.json.

For each institution that has the target topic, this script paginates
through all of their works matching that topic and sums cited_by_count.
This gives a citation ranking scoped to the topic, rather than using
each institution's total all-time citation count.

Designed to run infrequently (hours or days). Supports resuming from a
checkpoint file if interrupted.

Usage:
    python3 scripts/fetch_ai_rankings.py

Configuration is at the top of the file.
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Topic ID(s) to filter by. Use pipe-separated values for OR, e.g.:
#   TOPIC_FILTER = "topics.id:T10320|T10181"
TOPIC_ID = "T10320"
TOPIC_FILTER = f"topics.id:{TOPIC_ID}"

# OpenAlex polite pool — include your email for higher rate limits
MAILTO = "ui@openalex.org"

# How many API requests to make per second. OpenAlex's polite pool allows
# up to ~10/s, but we default to 2/s to be a considerate API citizen.
# Lower this if you see 429 errors piling up.
REQUESTS_PER_SECOND = 2.0

# Works per page when paginating (max 200)
WORKS_PER_PAGE = 200

# Institution select fields (keep small to reduce payload)
INSTITUTION_SELECT = "id,display_name,country_code,type,cited_by_count"

BASE_URL = "https://api.openalex.org"

# Paths (relative to this script's parent directory, i.e. the project root)
PROJECT_ROOT = Path(__file__).parent.parent
OUTPUT_FILE = PROJECT_ROOT / "public" / "ai-rankings-cache.json"
CHECKPOINT_FILE = PROJECT_ROOT / "ai-rankings-checkpoint.json"
LOG_FILE = PROJECT_ROOT / "ai-rankings.log"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_min_interval = 1.0 / REQUESTS_PER_SECOND
_last_request_time = 0.0


def api_get(path, params=None, retries=6):
    """
    GET {BASE_URL}{path} with:
      - polite rate limiting
      - exponential backoff on 5xx errors
      - long back-off on 429 rate-limit responses
    Returns parsed JSON or None on permanent failure.
    """
    global _last_request_time

    params = dict(params or {})
    params["mailto"] = MAILTO
    url = f"{BASE_URL}{path}"

    for attempt in range(retries):
        # Enforce rate limit
        elapsed = time.monotonic() - _last_request_time
        if elapsed < _min_interval:
            time.sleep(_min_interval - elapsed)

        try:
            _last_request_time = time.monotonic()
            resp = requests.get(url, params=params, timeout=60)
        except requests.RequestException as exc:
            wait = 15 * (2 ** attempt)
            log.warning(f"Network error (attempt {attempt+1}/{retries}): {exc} — retrying in {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            # Respect Retry-After header if present, otherwise back off hard
            retry_after = int(resp.headers.get("Retry-After", 120 * (attempt + 1)))
            log.warning(f"429 Too Many Requests — waiting {retry_after}s (attempt {attempt+1}/{retries})")
            time.sleep(retry_after)
            continue

        if resp.status_code >= 500:
            wait = 20 * (2 ** attempt)
            log.warning(f"HTTP {resp.status_code} (attempt {attempt+1}/{retries}) — retrying in {wait}s")
            time.sleep(wait)
            continue

        # 4xx that isn't 429: not retryable
        log.error(f"HTTP {resp.status_code} for {url} — {resp.text[:300]}")
        return None

    log.error(f"All {retries} retries exhausted for {url}")
    return None


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def fetch_all_institutions():
    """
    Return a list of all institutions that have the target topic,
    fetched in pages of 200 and sorted by total cited_by_count descending.
    """
    institutions = []
    page = 1

    log.info(f"Fetching institutions matching filter: {TOPIC_FILTER}")

    while True:
        data = api_get("/institutions", {
            "filter": TOPIC_FILTER,
            "sort": "cited_by_count:desc",
            "select": INSTITUTION_SELECT,
            "per-page": 200,
            "page": page,
        })

        if not data:
            log.error("Failed to fetch institutions page — aborting institution fetch.")
            break

        results = data.get("results", [])
        if not results:
            break

        institutions.extend(results)
        total = data["meta"]["count"]
        log.info(f"  Page {page}: {len(institutions):,} / {total:,} institutions fetched")

        if len(institutions) >= total:
            break

        page += 1

    log.info(f"Institution list complete: {len(institutions):,} institutions.")
    return institutions


def fetch_topic_citations_for_institution(inst_id):
    """
    For a single institution, paginate through all works that match the
    topic filter authored by that institution, and return the sum of
    each work's cited_by_count.

    Uses authorships.institutions.lineage so that sub-institutions
    (e.g. "MIT CSAIL") are rolled up into their parent.
    """
    short_id = inst_id.split("/")[-1]   # e.g. "I63966007"
    total_citations = 0
    total_works_seen = 0
    page = 1

    while True:
        data = api_get("/works", {
            "filter": f"authorships.institutions.lineage:{short_id},{TOPIC_FILTER}",
            "select": "cited_by_count",
            "per-page": WORKS_PER_PAGE,
            "page": page,
        })

        if not data:
            log.warning(f"  Failed to fetch works page {page} for {short_id} — stopping early.")
            break

        results = data.get("results", [])
        if not results:
            break

        batch_citations = sum(w.get("cited_by_count", 0) for w in results)
        total_citations += batch_citations
        total_works_seen += len(results)

        declared_total = data["meta"].get("count", 0)

        if page == 1:
            log.info(f"  {short_id}: {declared_total:,} matching works to process")

        if total_works_seen >= declared_total:
            break

        page += 1

    return total_citations


# ---------------------------------------------------------------------------
# Checkpoint helpers
# ---------------------------------------------------------------------------

def load_checkpoint():
    if CHECKPOINT_FILE.exists():
        with open(CHECKPOINT_FILE) as f:
            checkpoint = json.load(f)
        done = len(checkpoint.get("completed", {}))
        total = len(checkpoint.get("institutions") or [])
        log.info(f"Loaded checkpoint: {done}/{total} institutions already done.")
        return checkpoint
    return {"institutions": None, "completed": {}}


def save_checkpoint(checkpoint):
    with open(CHECKPOINT_FILE, "w") as f:
        json.dump(checkpoint, f)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("AI Rankings pre-fetch script")
    log.info(f"  Topic filter : {TOPIC_FILTER}")
    log.info(f"  Rate limit   : {REQUESTS_PER_SECOND} req/s")
    log.info(f"  Output       : {OUTPUT_FILE}")
    log.info(f"  Checkpoint   : {CHECKPOINT_FILE}")
    log.info("=" * 60)

    checkpoint = load_checkpoint()

    # Step 1: Fetch the institution list (once; reused on resume)
    if checkpoint["institutions"] is None:
        institutions = fetch_all_institutions()
        if not institutions:
            log.error("No institutions found. Exiting.")
            return
        checkpoint["institutions"] = institutions
        save_checkpoint(checkpoint)
    else:
        institutions = checkpoint["institutions"]
        remaining = sum(
            1 for inst in institutions
            if inst["id"].split("/")[-1] not in checkpoint["completed"]
        )
        log.info(f"Resuming: {remaining:,} institutions still to process.")

    completed = checkpoint["completed"]
    total = len(institutions)

    # Step 2: For each institution, fetch topic-scoped citation count
    for i, inst in enumerate(institutions):
        short_id = inst["id"].split("/")[-1]

        if short_id in completed:
            continue

        log.info(f"[{i+1}/{total}] {inst['display_name']}  ({short_id})")

        cs_citations = fetch_topic_citations_for_institution(inst["id"])

        log.info(f"  → {cs_citations:,} topic-scoped citations")

        completed[short_id] = cs_citations
        checkpoint["completed"] = completed
        save_checkpoint(checkpoint)

    # Step 3: Assemble and sort final results
    results = []
    for inst in institutions:
        short_id = inst["id"].split("/")[-1]
        results.append({
            "id": inst["id"],
            "display_name": inst["display_name"],
            "country_code": inst.get("country_code"),
            "type": inst.get("type"),
            "cs_cited_by_count": completed.get(short_id, 0),
            "total_cited_by_count": inst.get("cited_by_count", 0),
        })

    results.sort(key=lambda x: x["cs_cited_by_count"], reverse=True)

    # Step 4: Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    output = {
        "topic_id": TOPIC_ID,
        "topic_filter": TOPIC_FILTER,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(results),
        "results": results,
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    log.info(f"Done. Wrote {len(results):,} institutions to {OUTPUT_FILE}")
    log.info(f"You can delete {CHECKPOINT_FILE} once you're happy with the output.")


if __name__ == "__main__":
    main()
