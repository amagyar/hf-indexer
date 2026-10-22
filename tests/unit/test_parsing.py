"""Unit tests for the pipeline parsing and Parquet typing logic.

Runs with the standard library only (no pytest dependency):

    python3 -m unittest discover -s tests/unit -v

Requires the pipeline deps (requests/pandas/pyarrow) for imports and the
builder test - the same set CI installs for the fixture generator.
"""

import gzip
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

import build_parquet as bp  # noqa: E402
import fetch_updates as fu  # noqa: E402


class ParseHasWeightsTest(unittest.TestCase):
    """The weight-presence rule: true iff any authoritative signal exists."""

    def test_readme_only_repo_is_false(self):
        m = {"id": "user/empty", "tags": ["transformers", "license:mit"],
             "downloads": 0, "likes": 0}
        self.assertIs(fu.model_to_record(m)["has_weights"], False)

    def test_safetensors_expansion_is_true(self):
        m = {"id": "org/real", "tags": ["safetensors"],
             "safetensors": {"total": 7_000_000_000}}
        self.assertIs(fu.model_to_record(m)["has_weights"], True)

    def test_gguf_expansion_is_true(self):
        m = {"id": "org/gguf", "tags": ["gguf"],
             "gguf": {"total": 8_000_000_000, "bits": 4}}
        self.assertIs(fu.model_to_record(m)["has_weights"], True)

    def test_format_tag_without_expansions_is_true(self):
        m = {"id": "org/onnx-export", "tags": ["onnx"]}
        self.assertIs(fu.model_to_record(m)["has_weights"], True)

    def test_size_tag_signal_is_true(self):
        m = {"id": "user/old-ckpt", "tags": ["pytorch", "size:3b"]}
        self.assertIs(fu.model_to_record(m)["has_weights"], True)

    def test_id_regex_signal_is_true(self):
        m = {"id": "user/thing-13b", "tags": []}
        self.assertIs(fu.model_to_record(m)["has_weights"], True)

    def test_record_schema_order_matches_record_keys(self):
        record = fu.model_to_record({"id": "org/x", "tags": ["gguf"],
                                     "gguf": {"total": 1}})
        self.assertEqual(list(record.keys()), list(fu.RECORD_KEYS))


class BuildParquetHasWeightsTest(unittest.TestCase):
    """The published column is a nullable boolean: null for un-swept records,
    never false (D2: unknown != confirmed-empty)."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="hfidx-unit-")
        self.addCleanup(shutil.rmtree, self.work, ignore_errors=True)

    def _state(self, path):
        records = [
            {"id": "org/swept-true", "author": "org", "url": "u",
             "size_b": 7.0, "format": "gguf", "license": "mit",
             "has_weights": True, "tags": [], "downloads": 1, "likes": 0,
             "created_at": "2026-01-01T00:00:00Z",
             "modified_at": "2026-01-02T00:00:00Z",
             "metrics_refreshed_at": "2026-01-03T00:00:00Z"},
            {"id": "org/swept-false", "author": "org", "url": "u",
             "size_b": None, "format": "unknown", "license": None,
             "has_weights": False, "tags": [], "downloads": 0, "likes": 0,
             "created_at": "2026-01-01T00:00:00Z",
             "modified_at": "2026-01-02T00:00:00Z",
             "metrics_refreshed_at": "2026-01-03T00:00:00Z"},
            # Legacy record: no has_weights key at all (not yet re-swept).
            {"id": "org/legacy-null", "author": "org", "url": "u",
             "size_b": 3.0, "format": "unknown", "license": "mit",
             "tags": [], "downloads": 5, "likes": 0,
             "created_at": "2026-01-01T00:00:00Z",
             "modified_at": "2026-01-02T00:00:00Z",
             "metrics_refreshed_at": "2026-01-03T00:00:00Z"},
        ]
        with gzip.open(path, "wt") as fh:
            for r in records:
                fh.write(json.dumps(r) + "\n")

    def test_column_is_nullable_bool(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        jsonl = os.path.join(self.work, "models.jsonl.gz")
        self._state(jsonl)
        written = bp.build_parquet(jsonl, os.path.join(self.work, "models.parquet"))
        table = pa.concat_tables([pq.read_table(p) for p in written])

        self.assertEqual(table.schema.field("has_weights").type, "bool")
        values = dict(zip(table.column("id").to_pylist(),
                          table.column("has_weights").to_pylist()))
        self.assertIs(values["org/swept-true"], True)
        self.assertIs(values["org/swept-false"], False)
        self.assertIsNone(values["org/legacy-null"], "un-swept must be null, not false")


if __name__ == "__main__":
    unittest.main()
