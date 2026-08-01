"""Build typed, zstd-compressed Parquet shards from the sharded JSONL state.

Reads the gzip-compressed JSONL shards produced by `fetch_updates.py`
(`models-000.jsonl.gz` .. `models-NNN.jsonl.gz`), emits only the frontend-facing
columns, coerces each to a DuckDB-friendly type, and writes sharded
`models-NNN.parquet` (by crc32(id) % N) using the pyarrow engine with zstd
compression.
"""

from __future__ import annotations

import argparse
import glob
import logging
import os
import sys
import zlib
from typing import List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger("build_parquet")

# Canonical column order, matching RECORD_KEYS in fetch_updates.py. Listed
# explicitly so the Parquet schema stays stable across the `quant` -> `format`
# rename transition (legacy records carry `quant`, new ones carry
# `format` + `license`).
CANONICAL_COLUMNS = (
    "id", "author", "url", "size_b", "format", "license", "tags",
    "downloads", "likes", "created_at", "modified_at", "metrics_refreshed_at",
)
# Legacy fields dropped during normalization.
LEGACY_COLUMNS = ("quant",)

# Columns actually emitted to the published Parquet (everything the browser
# queries). Kept minimal for file size: `url` is reconstructed from `id` in the
# frontend, and `author` / `tags` / `metrics_refreshed_at` are not used by any
# frontend query. The full set still lives in the sharded JSONL state.
PARQUET_COLUMNS = (
    "id", "size_b", "format", "license",
    "downloads", "likes", "created_at", "modified_at",
)

# The published Parquet is itself sharded (by crc32(id) % PARQUET_SHARD_COUNT)
# so each file stays under GitHub Pages' 100 MB per-file limit as the catalog
# grows. Fewer shards than the JSONL state on purpose: the browser fans out a
# range request per shard per query, so we keep this small to bound latency.
PARQUET_SHARD_COUNT = 4
PARQUET_SHARD_WIDTH = 3

# Explicit column dtypes for DuckDB / Parquet optimization.
NUMERIC_INT_COLS = ("downloads", "likes")
NUMERIC_FLOAT_COLS = ("size_b",)
TIMESTAMP_COLS = ("created_at", "modified_at", "metrics_refreshed_at")
LIST_COLS = ("tags",)


def discover_input_shards(input_path: str) -> List[str]:
    """Return the JSONL.gz files to read: sharded `models-NNN.jsonl.gz` matching
    the input prefix, falling back to the legacy single `models.jsonl.gz`."""
    directory = os.path.dirname(os.path.abspath(input_path)) or "."
    base = os.path.basename(input_path)
    prefix = base[: -len(".jsonl.gz")] if base.endswith(".jsonl.gz") else base
    shards = sorted(glob.glob(os.path.join(directory, f"{prefix}-*.jsonl.gz")))
    if shards:
        return shards
    legacy = os.path.join(directory, f"{prefix}.jsonl.gz")
    return [legacy] if os.path.exists(legacy) else []


def load_dataframe(jsonl_path: str) -> pd.DataFrame:
    """Read sharded gzip-JSONL into a canonicalized DataFrame."""
    paths = discover_input_shards(jsonl_path)
    if not paths:
        raise SystemExit(f"Input not found: {jsonl_path} (no shards or legacy file)")
    LOG.info("Reading %d shard(s): %s", len(paths),
             [os.path.basename(p) for p in paths])
    frames = [pd.read_json(p, lines=True, compression="gzip") for p in paths]
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    df = normalize_columns(df)
    LOG.info("Loaded %d rows.", len(df))
    return df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize a raw JSONL DataFrame to the canonical column schema.

    Handles the `quant` -> `format` transition: legacy records carried a `quant`
    key (always "unknown" due to a prior tag-fetch bug) and no `format` /
    `license`. We drop `quant`, backfill any missing canonical column, and fix
    the column order so the Parquet schema is stable regardless of how many
    records have been re-swept yet.
    """
    dropped = [c for c in LEGACY_COLUMNS if c in df.columns]
    if dropped:
        LOG.info("Dropping legacy column(s) %s during normalization.", dropped)
        df = df.drop(columns=dropped)
    for col in CANONICAL_COLUMNS:
        if col not in df.columns:
            df[col] = None
    return df[list(CANONICAL_COLUMNS)]


def coerce_types(df: pd.DataFrame) -> pd.DataFrame:
    """Apply DuckDB-friendly column types."""
    for col in NUMERIC_INT_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype("int64")
    for col in NUMERIC_FLOAT_COLS:
        if col in df.columns:
            # float32 per spec - small size, fine for parameter counts.
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")
    for col in TIMESTAMP_COLS:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", utc=True)
    for col in LIST_COLS:
        if col in df.columns:
            df[col] = df[col].apply(lambda v: v if isinstance(v, list) else [])
    return df


def to_arrow_schema(df: pd.DataFrame) -> pa.Table:
    """Build an explicit Arrow schema with the desired types."""
    fields = []
    for name in df.columns:
        if name in NUMERIC_INT_COLS:
            fields.append(pa.field(name, pa.int64()))
        elif name in NUMERIC_FLOAT_COLS:
            fields.append(pa.field(name, pa.float32()))
        elif name in TIMESTAMP_COLS:
            fields.append(pa.field(name, pa.timestamp("us", tz="UTC")))
        elif name in LIST_COLS:
            fields.append(pa.field(name, pa.list_(pa.string())))
        else:
            fields.append(pa.field(name, pa.string()))
    schema = pa.schema(fields)
    return pa.Table.from_pandas(df, schema=schema, preserve_index=False)


def parquet_shard_for_id(model_id) -> int:
    """Deterministic shard index for a model id (mirrors the JSONL sharding)."""
    return zlib.crc32(str(model_id or "").encode("utf-8")) % PARQUET_SHARD_COUNT


def _parquet_prefix(path: str) -> str:
    """Derive the shard-name prefix from an `--output` path.

    Accepts both a bare prefix (`models`) and a legacy full name
    (`models.parquet`); shards are written as `<prefix>-NNN.parquet`.
    """
    base = os.path.basename(path)
    if base.endswith(".parquet"):
        base = base[: -len(".parquet")]
    return base


def build_parquet(jsonl_path: str, parquet_path: str) -> List[str]:
    df = load_dataframe(jsonl_path)
    # Trim to the frontend-facing columns (url/author/tags/metrics_refreshed_at
    # stay only in the JSONL state).
    df = df[list(PARQUET_COLUMNS)]
    df = coerce_types(df)

    directory = os.path.dirname(os.path.abspath(parquet_path)) or "."
    prefix = _parquet_prefix(parquet_path)
    # Bucket rows by shard so each output file is a self-contained slice.
    df = df.assign(__shard=df["id"].map(parquet_shard_for_id))

    LOG.info("Writing %d parquet shards (zstd compression).", PARQUET_SHARD_COUNT)
    written: List[str] = []
    for shard in range(PARQUET_SHARD_COUNT):
        sub = df[df["__shard"] == shard].drop(columns=["__shard"])
        shard_path = os.path.join(directory, f"{prefix}-{shard:0{PARQUET_SHARD_WIDTH}d}.parquet")
        table = to_arrow_schema(sub)
        pq.write_table(table, shard_path, compression="zstd")
        size_mb = os.path.getsize(shard_path) / (1024 * 1024)
        LOG.info("Wrote %s (%d rows, %.2f MB).", os.path.basename(shard_path), len(sub), size_mb)
        written.append(shard_path)
    return written


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("HF_INDEXER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="models.jsonl.gz", help="Input JSONL.gz path.")
    parser.add_argument("--output", default="models.parquet",
                        help="Output Parquet path prefix; sharded into models-NNN.parquet.")
    args = parser.parse_args(argv)
    build_parquet(args.input, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
