"""Generate deterministic local catalogs for the fixture-based E2E suite.

Builds three servable site dirs (Parquet shards + a copy of the frontend):

  serve-new/  460 models, current schema incl. `has_weights`:
                orgGood  300  has_weights=true   downloads 0..299
                orgSpam  120  has_weights=false  downloads 0..19
                orgNull   40  has_weights=null   (legacy, field omitted)
  serve-old/  same 460 models WITHOUT `has_weights` (tests the frontend
              column feature-detect against pre-change published data)
  serve-big/  150,000 models (tests the export row cap)

The row counts and distributions are part of the contract asserted by
tests/e2e/features.spec.js - change them there if you change them here.

Usage:
    python3 tests/e2e/fixtures/generate_fixtures.py [--output DIR]

Writes into DIR (default: tests/e2e/fixtures/dist). Serve each dir with
`python3 -m http.server <port> --directory <dir>` and run Playwright with
SITE_URL / SITE_URL_OLD / SITE_URL_BIG pointing at the ports.
"""

import argparse
import gzip
import json
import os
import shutil
import sys

# repo/tests/e2e/fixtures/generate_fixtures.py -> repo/
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import build_parquet as bp  # noqa: E402

FRONTEND_FILES = ("index.html", "app.js", "styles.css", "icon.svg")


def record(rid, **over):
    r = {
        "id": rid, "author": rid.split("/")[0],
        "url": f"https://huggingface.co/{rid}", "size_b": 7.0, "format": "gguf",
        "license": "apache-2.0", "has_weights": True, "tags": ["gguf"],
        "downloads": 0, "likes": 0, "created_at": "2025-03-01T00:00:00Z",
        "modified_at": "2026-09-01T00:00:00Z",
        "metrics_refreshed_at": "2026-10-01T00:00:00Z",
    }
    r.update(over)
    return r


def build_records():
    """The 460-record deterministic catalog (see module docstring)."""
    records = []
    for i in range(300):  # orgGood: weighted, llama/gguf/apache, downloads 0..299
        license_ = "apache-2.0"
        if i == 299:
            license_ = "apache-2.0, mit"  # comma: exercises CSV quoting
        elif i == 298:
            license_ = 'say "hi" license'  # quotes: exercises CSV quote-doubling
        records.append(record(
            f"orgGood/llama-{i:03d}",
            size_b=7.0 + (i % 13),
            format="gguf" if i % 2 else "safetensors",
            license=license_,
            has_weights=True, downloads=i, likes=i % 17,
        ))
    for i in range(120):  # orgSpam: README-only, downloads 0..19
        records.append(record(
            f"orgSpam/fake-model-{i:03d}",
            size_b=None, format="unknown", license=None, has_weights=False,
            tags=[], downloads=i % 20, likes=0,
            created_at="2026-08-01T00:00:00Z", modified_at="2026-10-01T00:00:00Z",
        ))
    for i in range(40):  # orgNull: legacy records not yet re-swept (no field)
        records.append(record(
            f"orgNull/legacy-ckpt-{i:03d}",
            size_b=3.0, format="unknown", license="mit", tags=[],
            downloads=50, likes=1,
            created_at="2024-06-01T00:00:00Z", modified_at="2026-07-01T00:00:00Z",
            _omit_has_weights=True,
        ))
    return records


def write_site(directory, records, include_has_weights=True):
    os.makedirs(directory, exist_ok=True)
    with gzip.open(os.path.join(directory, "models.jsonl.gz"), "wt") as fh:
        for r in records:
            out = dict(r)
            if out.pop("_omit_has_weights", False) or not include_has_weights:
                out.pop("has_weights", None)
            fh.write(json.dumps(out) + "\n")
    original_columns = bp.PARQUET_COLUMNS
    if not include_has_weights:
        bp.PARQUET_COLUMNS = tuple(c for c in original_columns if c != "has_weights")
    try:
        bp.build_parquet(os.path.join(directory, "models.jsonl.gz"),
                         os.path.join(directory, "models.parquet"))
    finally:
        bp.PARQUET_COLUMNS = original_columns
    for name in FRONTEND_FILES:
        shutil.copy(os.path.join(REPO_ROOT, "frontend", name),
                    os.path.join(directory, name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",
                        default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist"),
                        help="Output directory (default: tests/e2e/fixtures/dist)")
    args = parser.parse_args()

    out = args.output
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)

    records = build_records()
    write_site(os.path.join(out, "serve-new"), records, include_has_weights=True)
    write_site(os.path.join(out, "serve-old"), records, include_has_weights=False)

    big = [record(f"orgBig/model-{i:06d}", size_b=1.0 + (i % 70) / 10,
                  format="gguf" if i % 2 else "safetensors",
                  has_weights=True, downloads=150_000 - i, likes=i % 5000)
           for i in range(150_000)]
    write_site(os.path.join(out, "serve-big"), big, include_has_weights=True)

    print(f"Fixtures written to {out}: serve-new (460), serve-old (460, no "
          f"has_weights), serve-big (150,000).")


if __name__ == "__main__":
    main()
