"""Fetch and update Hugging Face model metadata into a compressed JSONL state file.

Each run:
  1. Downloads the current `models.jsonl.gz` from GitHub Pages (cache-busted).
  2. On 404, bootstraps from an empty state.
  3. Queries Hugging Face Hub for models modified after the most recent
     `modified_at` watermark in state (or all models with `--backfill`).
  4. Parses quantization and parameter size from tags / model id.
  5. Writes the merged state back to `models.jsonl.gz` (gzip, atomic).
"""

from __future__ import annotations

import argparse
import gzip
import io
import json
import logging
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

import requests

LOG = logging.getLogger("fetch_updates")

# Where the previous state is served from. Override with HF_INDEXER_PAGES_URL.
DEFAULT_PAGES_URL_TEMPLATE = "https://{owner}.github.io/{repo}/models.jsonl.gz"

# Known quantization formats in priority/identity order.
KNOWN_QUANTS = ("gguf", "awq", "gptq", "exl2")

# Retry settings for HTTP 429 from the Hugging Face API.
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0
BACKOFF_CAP_SECONDS = 60.0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# A model record. Keys are the canonical JSONL schema.
RECORD_KEYS = (
    "id",
    "author",
    "url",
    "size_b",
    "quant",
    "tags",
    "downloads",
    "likes",
    "created_at",
    "modified_at",
)


# ---------------------------------------------------------------------------
# State I/O
# ---------------------------------------------------------------------------


def load_remote_state(pages_url: str) -> Dict[str, Dict[str, Any]]:
    """Fetch `models.jsonl.gz` from GitHub Pages with cache-busting.

    Returns an empty dict on HTTP 404 (first-run bootstrap). Raises on other
    HTTP errors so CI fails loudly.
    """
    cache_bust = f"{pages_url}?t={int(time.time())}"
    LOG.info("Fetching state: %s", cache_bust)
    resp = requests.get(cache_bust, timeout=60)
    if resp.status_code == 404:
        LOG.warning("Remote state returned 404 - bootstrapping from empty state.")
        return {}
    resp.raise_for_status()

    state: Dict[str, Dict[str, Any]] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(resp.content)) as gz:
        for raw in gz:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            state[record["id"]] = record
    LOG.info("Loaded %d existing records from remote state.", len(state))
    return state


def write_local_state(state: Dict[str, Dict[str, Any]], path: str) -> None:
    """Serialize state to `path` as gzip-compressed JSONL atomically."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".models.", suffix=".jsonl.gz", dir=directory)
    try:
        with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb") as gz:
            for record in state.values():
                gz.write((json.dumps(record) + "\n").encode("utf-8"))
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    LOG.info("Wrote %d records to %s", len(state), path)


# ---------------------------------------------------------------------------
# Hugging Face API with rate-limit handling
# ---------------------------------------------------------------------------


class RateLimitExhausted(RuntimeError):
    """Raised when the HTTP 429 retry budget is exhausted."""


def _iter_models_retry(api, **list_kwargs) -> Iterable[Any]:
    """Wrap an `HfApi.list_models` iterator with bounded exponential backoff on 429."""
    backoff = INITIAL_BACKOFF_SECONDS
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            yield from api.list_models(**list_kwargs)
            return
        except Exception as exc:  # noqa: BLE001 - huggingface_hub surfaces requests errors
            last_exc = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status != 429:
                raise
            sleep = min(backoff, BACKOFF_CAP_SECONDS)
            LOG.warning("HTTP 429 from HF API (attempt %d/%d). Sleeping %.1fs.",
                        attempt + 1, MAX_RETRIES, sleep)
            time.sleep(sleep)
            backoff *= BACKOFF_MULTIPLIER
    raise RateLimitExhausted(f"HTTP 429 persisted after {MAX_RETRIES} retries") from last_exc


# ---------------------------------------------------------------------------
# Parsing: quantization & size
# ---------------------------------------------------------------------------


def parse_quant(tags: Iterable[str]) -> str:
    """Return the first known quantization tag, or 'unknown'."""
    tag_set = {t.lower() for t in (tags or [])}
    for q in KNOWN_QUANTS:
        if q in tag_set:
            return q
    return "unknown"


# Matches: "7b", "0.5b", "70b" anywhere in the id, optionally prefixed by "-" or "_".
_SIZE_ID_RE = re.compile(r"(?<![0-9a-zA-Z])(\d+(?:\.\d+)?)\s*[bB]\b", re.IGNORECASE)
# Matches MoE-style: "8x7b", "2x14b", "8 x 7 b"
_MOE_RE = re.compile(
    r"(?<![0-9a-zA-Z])(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*[bB]\b"
)
# Authoritative tag form: "size:7b", "size:0.5b"
_SIZE_TAG_RE = re.compile(r"^size:(\d+(?:\.\d+)?)b$", re.IGNORECASE)


def parse_size(tags: Iterable[str], model_id: str) -> Optional[float]:
    """Derive parameter size in billions.

    1. Prefer `size:<n>b` tags (authoritative).
    2. Fall back to MoE pattern `NxNb` in the model id (multiply).
    3. Fall back to plain `<n>b` in the model id.
    4. None if nothing matches.
    """
    for tag in tags or []:
        m = _SIZE_TAG_RE.match(tag.strip())
        if m:
            return float(m.group(1))

    if model_id:
        moe = _MOE_RE.search(model_id)
        if moe:
            return float(moe.group(1)) * float(moe.group(2))
        m = _SIZE_ID_RE.search(model_id)
        if m:
            return float(m.group(1))
    return None


# ---------------------------------------------------------------------------
# Model -> record
# ---------------------------------------------------------------------------


def model_to_record(model: Any) -> Dict[str, Any]:
    """Convert a huggingface_hub model object to our JSONL record dict."""
    model_id = getattr(model, "id", "") or ""
    author = model_id.split("/", 1)[0] if "/" in model_id else ""
    tags = list(getattr(model, "tags", None) or [])
    last_modified = getattr(model, "lastModified", None)
    created_at = getattr(model, "createdAt", None) or last_modified

    return {
        "id": model_id,
        "author": author,
        "url": f"https://huggingface.co/{model_id}",
        "size_b": parse_size(tags, model_id),
        "quant": parse_quant(tags),
        "tags": tags,
        "downloads": int(getattr(model, "downloads", 0) or 0),
        "likes": int(getattr(model, "likes", 0) or 0),
        "created_at": _iso_z(created_at),
        "modified_at": _iso_z(last_modified),
    }


def _iso_z(value: Any) -> Optional[str]:
    """Normalize a datetime / ISO string to a UTC ISO-8601 string ending in Z."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(value)


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Watermark & fetching
# ---------------------------------------------------------------------------


def compute_watermark(state: Dict[str, Dict[str, Any]]) -> Optional[datetime]:
    """Return the maximum `modified_at` across state, or None if state is empty."""
    latest: Optional[datetime] = None
    for record in state.values():
        modified = _parse_iso(record.get("modified_at"))
        if modified is None:
            continue
        if latest is None or modified > latest:
            latest = modified
    return latest


def fetch_new_models(api, watermark: Optional[datetime], backfill: bool) -> List[Any]:
    """Fetch models from the Hub, stopping at the watermark when not backfilling."""
    list_kwargs: Dict[str, Any] = {
        "sort": "lastModified",
        "direction": -1,
        "full": bool(backfill),
    }
    LOG.info("Listing models (backfill=%s, watermark=%s)", backfill, watermark)

    results: List[Any] = []
    for model in _iter_models_retry(api, **list_kwargs):
        if not backfill and watermark is not None:
            modified = _parse_iso(_iso_z(getattr(model, "lastModified", None)))
            if modified is not None and modified <= watermark:
                LOG.info("Reached watermark at model %s - stopping.", getattr(model, "id", "?"))
                break
        results.append(model)
    LOG.info("Fetched %d model(s) from Hub.", len(results))
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def build_pages_url(args: argparse.Namespace) -> str:
    if args.pages_url:
        return args.pages_url.rstrip("/")
    if args.owner and args.repo:
        return DEFAULT_PAGES_URL_TEMPLATE.format(owner=args.owner, repo=args.repo)
    # Fall back to env vars (set by CI).
    owner = os.environ.get("HF_INDEXER_PAGES_OWNER")
    repo = os.environ.get("HF_INDEXER_PAGES_REPO")
    if owner and repo:
        return DEFAULT_PAGES_URL_TEMPLATE.format(owner=owner, repo=repo)
    raise SystemExit(
        "Cannot determine GitHub Pages URL. Pass --owner/--repo, --pages-url, "
        "or set HF_INDEXER_PAGES_OWNER and HF_INDEXER_PAGES_REPO."
    )


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("HF_INDEXER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", help="GitHub repo owner (for default Pages URL).")
    parser.add_argument("--repo", help="GitHub repo name (for default Pages URL).")
    parser.add_argument("--pages-url", help="Full URL to remote models.jsonl.gz.")
    parser.add_argument(
        "--output",
        default="models.jsonl.gz",
        help="Local output path for the updated state (default: models.jsonl.gz).",
    )
    parser.add_argument(
        "--backfill",
        action="store_true",
        help="Ignore the watermark and fetch full metadata for all models.",
    )
    args = parser.parse_args(argv)

    pages_url = build_pages_url(args)
    state = load_remote_state(pages_url)

    from huggingface_hub import HfApi

    api = HfApi()
    watermark = None if args.backfill else compute_watermark(state)
    new_models = fetch_new_models(api, watermark, args.backfill)

    updated = 0
    for model in new_models:
        record = model_to_record(model)
        if not record["id"]:
            continue
        state[record["id"]] = record
        updated += 1

    LOG.info("Updated %d record(s). Total state size: %d.", updated, len(state))
    write_local_state(state, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
