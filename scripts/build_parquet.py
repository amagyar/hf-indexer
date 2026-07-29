"""Build a typed, zstd-compressed Parquet file from `models.jsonl.gz`.

Reads the gzip-compressed JSONL state produced by `fetch_updates.py`,
coerces each column to a DuckDB-friendly type, and writes `models.parquet`
using the pyarrow engine with zstd compression.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

LOG = logging.getLogger("build_parquet")

# Explicit column dtypes for DuckDB / Parquet optimization.
NUMERIC_INT_COLS = ("downloads", "likes")
NUMERIC_FLOAT_COLS = ("size_b",)
TIMESTAMP_COLS = ("created_at", "modified_at", "metrics_refreshed_at")
LIST_COLS = ("tags",)


def load_dataframe(jsonl_path: str) -> pd.DataFrame:
    """Read gzip-compressed JSONL into a DataFrame."""
    if not os.path.exists(jsonl_path):
        raise SystemExit(f"Input not found: {jsonl_path}")
    LOG.info("Reading %s", jsonl_path)
    df = pd.read_json(jsonl_path, lines=True, compression="gzip")
    LOG.info("Loaded %d rows.", len(df))
    return df


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


def build_parquet(jsonl_path: str, parquet_path: str) -> None:
    df = load_dataframe(jsonl_path)
    df = coerce_types(df)
    table = to_arrow_schema(df)
    LOG.info("Writing %s (zstd compression).", parquet_path)
    pq.write_table(table, parquet_path, compression="zstd")
    size_mb = os.path.getsize(parquet_path) / (1024 * 1024)
    LOG.info("Wrote %s (%.2f MB).", parquet_path, size_mb)


def main(argv: Optional[list] = None) -> int:
    logging.basicConfig(
        level=os.environ.get("HF_INDEXER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="models.jsonl.gz", help="Input JSONL.gz path.")
    parser.add_argument("--output", default="models.parquet", help="Output Parquet path.")
    args = parser.parse_args(argv)
    build_parquet(args.input, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
