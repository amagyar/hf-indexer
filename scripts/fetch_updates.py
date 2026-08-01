"""Fetch Hugging Face model metadata into a compressed JSONL state file.

Strategy
--------
Two passes run each invocation:

1. **Incremental pass** (catches new/updated models):
   - Iterate the Hub from newest (no cursor), sorted by `lastModified` desc.
   - Stop at the `modified_at` watermark (the newest model already in state).
   - Naturally bounded - only catches models modified since the last run.

2. **Backfill / metrics-sweep pass** (catches older historical models, then refreshes popularity):
   - Resume from a persisted pagination `cursor` (the HF API's `Link: rel="next"`
     cursor, which encodes `lastModified < <timestamp>`).
   - Fetch up to `--limit` more models this run, then persist the new cursor.
   - When the API returns no next cursor, backfill is marked **complete** and the
     cursor resets to newest - the pass then keeps cycling through the catalog as
     a **metrics sweep**, refreshing `downloads`/`likes` (which drift continuously
     and are independent of `lastModified`). At `--limit 50000`/hour over ~1M
     models, every record's counters refresh roughly once per day.

The state is sharded across `models-000.jsonl.gz` .. `models-007.jsonl.gz` in
`backfill_state.json`'s directory so each published file stays under GitHub
Pages' 100 MB per-file limit; each run resumes exactly where the last stopped -
no re-iteration, no quadratic cost.

Why raw `requests` (not `huggingface_hub`):
  - The list endpoint drops base fields (`downloads`/`likes`/`createdAt`/`tags`)
    whenever `expand` is set, so we issue two calls per page (base + expansions)
    and merge by id. The library's iterator hides both the cursor and this quirk.
  - We must persist the pagination cursor to resume iterative backfill efficiently.
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
import zlib
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

import requests

LOG = logging.getLogger("fetch_updates")

HF_API = "https://huggingface.co/api/models"
PAGE_SIZE = 1000  # models per API request page
DEFAULT_BACKFILL_LIMIT = 50_000

# Where state is served from. Override with --pages-base or --pages-url.
DEFAULT_PAGES_BASE_TEMPLATE = "https://{owner}.github.io/{repo}"

STATE_FILENAME = "models.jsonl.gz"
BACKFILL_FILENAME = "backfill_state.json"

# State is sharded across this many gzip-JSONL files to keep each published
# file under GitHub Pages' 100 MB per-file limit as the catalog grows. Records
# are distributed by crc32(id) % SHARD_COUNT so each shard is ~total/SHARD_COUNT
# and a given id always maps to the same shard. 8 shards ~= 13 MB/shard at ~3M
# models, with headroom for several-fold growth.
SHARD_COUNT = 8
SHARD_WIDTH = 3  # zero-padding in shard filenames (models-000.jsonl.gz)

# Retry settings for HTTP 429 from the Hugging Face API.
MAX_RETRIES = 5
INITIAL_BACKOFF_SECONDS = 2.0
BACKOFF_MULTIPLIER = 2.0
BACKOFF_CAP_SECONDS = 60.0

# Known model / weight formats. The first match in tag order below wins, so
# the list is ordered by specificity (file-container formats first, then
# quant methods, then runtime / export formats).
KNOWN_FORMATS = (
    "gguf",
    "awq",
    "gptq",
    "exl2",
    "compressed-tensors",
    "bitsandbytes",
    "mlx",
    "bitnet",
    "onnx",
)

# Canonical JSONL schema order.
RECORD_KEYS = (
    "id", "author", "url", "size_b", "format", "license", "tags",
    "downloads", "likes", "created_at", "modified_at", "metrics_refreshed_at",
)


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------


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
# Parsing: format, license & size
# ---------------------------------------------------------------------------


def parse_format(tags: Iterable[str]) -> str:
    """Return the first known model-format tag, or 'unknown'."""
    tag_set = {t.lower() for t in (tags or [])}
    for fmt in KNOWN_FORMATS:
        if fmt in tag_set:
            return fmt
    return "unknown"


_LICENSE_TAG_RE = re.compile(r"^license:(.+)$", re.IGNORECASE)


def parse_license(card_data: Optional[Dict[str, Any]],
                  tags: Iterable[str]) -> Optional[str]:
    """Derive the model license from `cardData`, falling back to tags.

    Priority:
      1. `cardData.license` (structured SPDX id, or a list for multi-licensed
         models). The placeholder value `"other"` is skipped in favor of (2).
      2. `cardData.license_name` (the human-readable name authors set when
         their license is not a standard SPDX id, i.e. `license: other`).
      3. A `license:<id>` tag.
      4. None.
    """
    if card_data:
        lic = card_data.get("license")
        if isinstance(lic, list):
            parts = [str(x).strip() for x in lic if str(x).strip()]
            if parts:
                return ", ".join(parts)
        elif isinstance(lic, str):
            lic = lic.strip()
            if lic and lic.lower() != "other":
                return lic
        name = card_data.get("license_name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    for tag in tags or []:
        m = _LICENSE_TAG_RE.match(tag.strip())
        if m:
            return m.group(1).strip()
    return None


_SIZE_ID_RE = re.compile(r"(?<![0-9a-zA-Z])(\d+(?:\.\d+)?)\s*[bB]\b")
_MOE_RE = re.compile(r"(?<![0-9a-zA-Z])(\d+(?:\.\d+)?)\s*[xX]\s*(\d+(?:\.\d+)?)\s*[bB]\b")
_SIZE_TAG_RE = re.compile(r"^size:(\d+(?:\.\d+)?)b$", re.IGNORECASE)


def parse_size(tags: Iterable[str], model_id: str,
               param_total: Optional[int] = None) -> Optional[float]:
    """Derive parameter size in billions.

    Priority:
      1. `param_total` (authoritative parameter count from the Hub's
         `safetensors.total` or `gguf.total` expansion) - the most accurate.
      2. A `size:<n>b` tag.
      3. Regex on the model id (`<n>b`, MoE `NxNb`).
      4. None if nothing matches.
    """
    if param_total:
        return round(float(param_total) / 1e9, 2)
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


def model_to_record(model: Dict[str, Any], captured_at: Optional[str] = None) -> Dict[str, Any]:
    """Convert a raw HF API model dict to our JSONL record dict.

    `captured_at` is an ISO-8601 UTC timestamp stamped on the record as
    `metrics_refreshed_at`, i.e. when its popularity counters were last
    refreshed from the Hub. Defaults to now.
    """
    model_id = model.get("id", "") or ""
    author = model.get("author") or (model_id.split("/", 1)[0] if "/" in model_id else "")
    tags = list(model.get("tags") or [])
    card_data = model.get("cardData") or {}
    last_modified = model.get("lastModified")
    created_at = model.get("createdAt") or last_modified
    # Authoritative parameter count (if the Hub exposed it via expand).
    # safetensors.total / gguf.total are both parameter counts.
    safetensors = model.get("safetensors") or {}
    gguf = model.get("gguf") or {}
    param_total = safetensors.get("total") or gguf.get("total")
    if captured_at is None:
        captured_at = _iso_z(datetime.now(timezone.utc))
    return {
        "id": model_id,
        "author": author,
        "url": f"https://huggingface.co/{model_id}",
        "size_b": parse_size(tags, model_id, param_total),
        "format": parse_format(tags),
        "license": parse_license(card_data, tags),
        "tags": tags,
        "downloads": int(model.get("downloads") or 0),
        "likes": int(model.get("likes") or 0),
        "created_at": _iso_z(created_at),
        "modified_at": _iso_z(last_modified),
        "metrics_refreshed_at": captured_at,
    }


# ---------------------------------------------------------------------------
# State I/O (sharded models-NNN.jsonl.gz + backfill_state.json)
# ---------------------------------------------------------------------------


def shard_for_id(model_id: str) -> int:
    """Deterministic shard index for a model id (crc32 -> even, stable hashing)."""
    return zlib.crc32((model_id or "").encode("utf-8")) % SHARD_COUNT


def shard_filename(shard: int) -> str:
    return f"models-{shard:0{SHARD_WIDTH}d}.jsonl.gz"


def _state_prefix(path: str) -> str:
    """Derive the shard-name prefix from an `--output` path.

    Accepts both a bare prefix (`models`) and a legacy full name
    (`models.jsonl.gz`); the shard files are written as `<prefix>-NNN.jsonl.gz`.
    """
    base = os.path.basename(path)
    if base.endswith(".jsonl.gz"):
        base = base[: -len(".jsonl.gz")]
    return base


def _http_get(url: str, timeout: int = 60) -> requests.Response:
    headers = {}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return requests.get(url, params={"t": int(time.time())}, headers=headers, timeout=timeout)


def _merge_jsonl_gz(state: Dict[str, Dict[str, Any]], content: bytes, label: str) -> int:
    """Decompress a gzip-JSONL blob and merge its records into `state`. Returns count."""
    count = 0
    with gzip.GzipFile(fileobj=io.BytesIO(content)) as gz:
        for raw in gz:
            raw = raw.strip()
            if not raw:
                continue
            record = json.loads(raw)
            state[record["id"]] = record
            count += 1
    return count


def load_remote_state(base_url: str) -> Dict[str, Dict[str, Any]]:
    """Fetch sharded state (`models-000.jsonl.gz` .. `models-NNN.jsonl.gz`) from
    `<base_url>/`. Falls back to the legacy single `models.jsonl.gz` for a
    one-time migration. 404 on everything -> empty state (bootstrap).
    """
    base = base_url.rstrip("/")
    state: Dict[str, Dict[str, Any]] = {}
    found_shards = 0
    for shard in range(SHARD_COUNT):
        url = f"{base}/{shard_filename(shard)}"
        resp = _http_get(url)
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        found_shards += 1
        _merge_jsonl_gz(state, resp.content, shard_filename(shard))
    if found_shards:
        LOG.info("Loaded %d existing records from %d shard(s).", len(state), found_shards)
        return state

    # Migration fallback: legacy single-file state.
    legacy_url = f"{base}/{STATE_FILENAME}"
    LOG.info("No shards found; trying legacy single-file state: %s", legacy_url)
    resp = _http_get(legacy_url)
    if resp.status_code == 404:
        LOG.warning("Remote state returned 404 - bootstrapping from empty state.")
        return {}
    resp.raise_for_status()
    n = _merge_jsonl_gz(state, resp.content, STATE_FILENAME)
    LOG.info("Loaded %d records from legacy state (will be re-sharded on write).", n)
    return state


def load_remote_backfill(base_url: str) -> Dict[str, Any]:
    """Fetch `backfill_state.json`. 404 / invalid -> default empty cursor."""
    url = f"{base_url.rstrip('/')}/{BACKFILL_FILENAME}"
    LOG.info("Fetching backfill state: %s", url)
    resp = _http_get(url)
    if resp.status_code == 404:
        LOG.warning("No remote backfill state - starting fresh.")
        return {"cursor": None, "complete": False}
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError:
        LOG.warning("Malformed backfill_state.json - starting fresh.")
        return {"cursor": None, "complete": False}
    return {
        "cursor": data.get("cursor"),
        "complete": bool(data.get("complete", False)),
    }


def write_local_state(state: Dict[str, Dict[str, Any]], path: str) -> List[str]:
    """Serialize state into `SHARD_COUNT` gzip-compressed JSONL shards, atomically.

    `path` is treated as a prefix: `--output models.jsonl.gz` produces
    `models-000.jsonl.gz` .. `models-007.jsonl.gz` in the same directory.
    Returns the list of written shard paths.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    prefix = _state_prefix(path)
    buckets: List[List[Dict[str, Any]]] = [[] for _ in range(SHARD_COUNT)]
    for record in state.values():
        buckets[shard_for_id(record.get("id", ""))].append(record)
    written: List[str] = []
    for shard, records in enumerate(buckets):
        shard_name = f"{prefix}-{shard:0{SHARD_WIDTH}d}.jsonl.gz"
        shard_path = os.path.join(directory, shard_name)
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{shard_name}.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb") as gz:
                for record in records:
                    gz.write((json.dumps(record) + "\n").encode("utf-8"))
            os.replace(tmp_path, shard_path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
        written.append(shard_path)
        LOG.info("Wrote %d records to %s", len(records), shard_path)
    return written


def write_local_backfill(backfill: Dict[str, Any], path: str) -> None:
    """Write the backfill cursor state atomically as JSON."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".backfill.", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(backfill, fh, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise
    LOG.info("Wrote backfill state to %s (complete=%s, cursor=%s)",
             path, backfill.get("complete"), "set" if backfill.get("cursor") else "none")


# ---------------------------------------------------------------------------
# HF API page fetch (raw HTTP, cursor pagination, 429 retry)
# ---------------------------------------------------------------------------


_NEXT_CURSOR_RE = re.compile(r'<([^>]+)>;\s*rel="next"')
_CURSOR_PARAM_RE = re.compile(r"[?&]cursor=([^>&]+)")


def _extract_next_cursor(link_header: Optional[str]) -> Optional[str]:
    """Pull the `cursor=...` value out of the `Link: rel="next"` URL."""
    if not link_header:
        return None
    nxt = _NEXT_CURSOR_RE.search(link_header)
    if not nxt:
        return None
    url = nxt.group(1)
    m = _CURSOR_PARAM_RE.search(url)
    return m.group(1) if m else None


class RateLimitExhausted(RuntimeError):
    """Raised when the HTTP 429 retry budget is exhausted."""


def _hf_get(params: Dict[str, str]) -> requests.Response:
    headers = {"Accept": "application/json"}
    token = os.environ.get("HF_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    backoff = INITIAL_BACKOFF_SECONDS
    last_exc: Optional[Exception] = None
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(HF_API, params=params, headers=headers, timeout=60)
            if resp.status_code == 429:
                raise _Retryable429(resp)
            return resp
        except _Retryable429 as exc:
            last_exc = exc
            sleep = min(backoff, BACKOFF_CAP_SECONDS)
            LOG.warning("HTTP 429 from HF API (attempt %d/%d). Sleeping %.1fs.",
                        attempt + 1, MAX_RETRIES, sleep)
            time.sleep(sleep)
            backoff *= BACKOFF_MULTIPLIER
        except requests.RequestException as exc:
            last_exc = exc
            sleep = min(backoff, BACKOFF_CAP_SECONDS)
            LOG.warning("Transient request error (attempt %d/%d): %s. Sleeping %.1fs.",
                        attempt + 1, MAX_RETRIES, exc, sleep)
            time.sleep(sleep)
            backoff *= BACKOFF_MULTIPLIER
    raise RateLimitExhausted(f"HF API request failed after {MAX_RETRIES} retries") from last_exc


class _Retryable429(Exception):
    def __init__(self, resp: requests.Response) -> None:
        super().__init__(f"HTTP 429: {resp.text[:200]}")
        self.response = resp


def fetch_hf_page(cursor: Optional[str] = None) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch one page of models. Returns (models, next_cursor).

    The HF list API has a painful quirk: specifying ANY `expand` makes it
    return ONLY `{_id, id, <sort field>}` plus the expansions, DROPPING the
    base fields (`downloads`, `likes`, `createdAt`, `author`, even `tags`).
    But we need both - the base fields for the metrics/dates and the
    `safetensors.total` / `gguf.total` expansions for authoritative `size_b`,
    plus `cardData` for `license`. (Note: `tags` is already part of the
    default response and does NOT need expanding.)

    So we issue two requests for the same page (identical sort + cursor) and
    merge by id: base fields from the no-expand call, expansions from the
    expand call. This doubles request volume vs a single call, but it is the
    only way to obtain both field sets from the list endpoint.
    """
    base_params: Dict[str, Any] = {
        "sort": "lastModified",
        "full": "false",
        "limit": str(PAGE_SIZE),
    }
    expand_params: Dict[str, Any] = {
        "sort": "lastModified",
        "full": "false",
        "limit": str(PAGE_SIZE),
        "expand": ["safetensors", "gguf", "cardData"],
    }
    if cursor:
        base_params["cursor"] = cursor
        expand_params["cursor"] = cursor

    base_resp = _hf_get(base_params)
    base_resp.raise_for_status()
    base_models = base_resp.json()
    if not isinstance(base_models, list):
        base_models = []
    next_cursor = _extract_next_cursor(base_resp.headers.get("link"))
    if not base_models:
        return [], next_cursor

    # Second request for the expansions on the same page; merge by id.
    expand_resp = _hf_get(expand_params)
    expand_resp.raise_for_status()
    expand_models = expand_resp.json()
    expand_by_id = {
        m.get("id"): m for m in expand_models
        if isinstance(m, dict) and m.get("id")
    }
    missing = 0
    for m in base_models:
        e = expand_by_id.get(m.get("id"))
        if not e:
            missing += 1
            continue
        m["safetensors"] = e.get("safetensors")
        m["gguf"] = e.get("gguf")
        m["cardData"] = e.get("cardData")
    if missing:
        LOG.warning("Expansions missing for %d/%d models on this page "
                    "(catalog shifted between requests); their size_b will "
                    "fall back to heuristics.", missing, len(base_models))
    return base_models, next_cursor


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------


def compute_watermark(state: Dict[str, Dict[str, Any]]) -> Optional[datetime]:
    """Return the maximum `modified_at` across state, or None if empty."""
    latest: Optional[datetime] = None
    for record in state.values():
        modified = _parse_iso(record.get("modified_at"))
        if modified is None:
            continue
        if latest is None or modified > latest:
            latest = modified
    return latest


# ---------------------------------------------------------------------------
# Passes
# ---------------------------------------------------------------------------


def incremental_pass(watermark: Optional[datetime]) -> List[Dict[str, Any]]:
    """Fetch new/updated models from newest, stopping at the watermark."""
    if watermark is None:
        LOG.info("Incremental pass skipped (no watermark / empty state).")
        return []
    LOG.info("Incremental pass: fetching models modified after %s", watermark)
    collected: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    while True:
        models, next_cursor = fetch_hf_page(cursor)
        if not models:
            break
        stop = False
        for m in models:
            modified = _parse_iso(m.get("lastModified"))
            if modified is not None and modified <= watermark:
                stop = True
                break
            collected.append(m)
        if stop or not next_cursor:
            break
        cursor = next_cursor
    LOG.info("Incremental pass collected %d new/updated model(s).", len(collected))
    return collected


def backfill_or_sweep_pass(start_cursor: Optional[str], batch_limit: int,
                           sweeping: bool = False) -> Tuple[List[Dict[str, Any]], Optional[str], bool]:
    """Fetch up to `batch_limit` models from the Hub, resuming from `start_cursor`.

    This single pass serves two purposes over the life of the index:
      - **Backfill** (before history is complete): advances the cursor through
        older models to fill in historical coverage.
      - **Metrics sweep** (after history is complete): keeps cycling through
        already-known models to refresh `downloads`/`likes`, which drift
        continuously and are NOT tied to `lastModified`.

    Returns (models, resume_cursor, exhausted):
      - resume_cursor: cursor to persist for the next run (None if exhausted).
      - exhausted: True if the API returned no next page (reached the oldest
        model). The caller resets the cursor to None so the next run restarts
        from newest - completing one backfill, or starting a new sweep cycle.
    """
    if batch_limit <= 0:
        LOG.info("%s pass disabled (batch_limit <= 0).",
                 "Sweep" if sweeping else "Backfill")
        return [], start_cursor, False
    LOG.info("%s pass: resuming from cursor=%s, batch_limit=%d",
             "Sweep" if sweeping else "Backfill",
             "set" if start_cursor else "newest", batch_limit)

    collected: List[Dict[str, Any]] = []
    cursor = start_cursor
    while len(collected) < batch_limit:
        models, next_cursor = fetch_hf_page(cursor)
        if not models:
            return collected, None, True  # exhausted
        collected.extend(models)
        if not next_cursor:
            return collected, None, True  # exhausted - no more pages
        cursor = next_cursor
    LOG.info("%s pass collected %d model(s); resume cursor set.",
             "Sweep" if sweeping else "Backfill", len(collected))
    return collected, cursor, False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def resolve_base_url(args: argparse.Namespace) -> str:
    if args.pages_base:
        return args.pages_base.rstrip("/")
    if args.pages_url:
        # Tolerate being handed the full models.jsonl.gz URL.
        return args.pages_url.rsplit("/", 1)[0]
    if args.owner and args.repo:
        return DEFAULT_PAGES_BASE_TEMPLATE.format(owner=args.owner, repo=args.repo)
    owner = os.environ.get("HF_INDEXER_PAGES_OWNER")
    repo = os.environ.get("HF_INDEXER_PAGES_REPO")
    if owner and repo:
        return DEFAULT_PAGES_BASE_TEMPLATE.format(owner=owner, repo=repo)
    raise SystemExit(
        "Cannot determine GitHub Pages base URL. Pass --owner/--repo, --pages-base, "
        "or set HF_INDEXER_PAGES_OWNER and HF_INDEXER_PAGES_REPO."
    )


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("HF_INDEXER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    src = parser.add_argument_group("source (GitHub Pages base)")
    src.add_argument("--owner", help="GitHub repo owner (for default Pages URL).")
    src.add_argument("--repo", help="GitHub repo name (for default Pages URL).")
    src.add_argument("--pages-base", help="Base URL of the GitHub Pages site.")
    src.add_argument("--pages-url", help="Full URL to remote models.jsonl.gz (base is derived).")

    out = parser.add_argument_group("output")
    out.add_argument("--output", default=STATE_FILENAME,
                     help="Local state path prefix; sharded into models-NNN.jsonl.gz.")
    out.add_argument("--backfill-output", default=BACKFILL_FILENAME,
                     help="Local backfill_state.json path.")

    run = parser.add_argument_group("run control")
    run.add_argument("--limit", type=int, default=DEFAULT_BACKFILL_LIMIT,
                     help=f"Max models to fetch in the backfill/sweep pass this run (default {DEFAULT_BACKFILL_LIMIT}).")
    run.add_argument("--no-backfill", action="store_true",
                     help="Skip the backfill/sweep pass (incremental updates only).")
    args = parser.parse_args(argv)

    base_url = resolve_base_url(args)

    state = load_remote_state(base_url)
    backfill = load_remote_backfill(base_url)

    # --- Incremental pass (newest -> watermark) ---
    watermark = compute_watermark(state)
    new_models = incremental_pass(watermark)

    # --- Backfill / metrics-sweep pass (resume from cursor, bounded by --limit) ---
    # Before history is complete this fills in older models (backfill). After
    # completion it keeps cycling through the catalog to refresh popularity
    # counters (metrics sweep). The pass always runs unless --no-backfill.
    resume_cursor = backfill.get("cursor")
    complete = bool(backfill.get("complete", False))
    sweep_models: List[Dict[str, Any]] = []
    if not args.no_backfill:
        sweep_models, resume_cursor, exhausted = backfill_or_sweep_pass(
            resume_cursor, args.limit, sweeping=complete
        )
        if exhausted:
            # Reached the oldest model. Mark backfill complete (idempotent) and
            # reset the cursor so the next run restarts from newest - beginning
            # a fresh metrics-sweep cycle.
            complete = True
            resume_cursor = None

    # --- Merge into state (stamp all refreshed records with the run timestamp) ---
    run_ts = _iso_z(datetime.now(timezone.utc))
    merged = 0
    for model in [*new_models, *sweep_models]:
        record = model_to_record(model, captured_at=run_ts)
        if not record["id"]:
            continue
        state[record["id"]] = record
        merged += 1

    LOG.info("Merged %d record(s). Total state size: %d.", merged, len(state))
    write_local_state(state, args.output)
    write_local_backfill({"cursor": resume_cursor, "complete": complete}, args.backfill_output)
    if complete:
        LOG.info("Backfill complete. Running continuous metrics sweep "
                 "(cursor cycles through the catalog to refresh downloads/likes).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
