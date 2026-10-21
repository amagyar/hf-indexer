# Spec Delta

## ADDED Requirements

### Requirement: Derive weight presence at parse time
The system SHALL derive a `has_weights` boolean for each record at parse time: true when any authoritative weight signal exists for the model (a `safetensors` expansion, a `gguf` expansion, a known weight-format tag, or a parseable parameter-size signal), and false when none exist (README-only repositories).

#### Scenario: README-only repository
- **WHEN** a model has no `safetensors` expansion, no `gguf` expansion, no known weight-format tag, and no parseable size
- **THEN** `has_weights` SHALL be false

#### Scenario: Model with safetensors weights
- **WHEN** the Hub returns a non-empty `safetensors` expansion for a model
- **THEN** `has_weights` SHALL be true

#### Scenario: Model with GGUF weights
- **WHEN** the Hub returns a non-empty `gguf` expansion for a model
- **THEN** `has_weights` SHALL be true

#### Scenario: Model with a weight-format tag but no expansions
- **WHEN** a model has no `safetensors`/`gguf` expansion but its tags include a known weight-format value (e.g. `onnx`, `mlx`)
- **THEN** `has_weights` SHALL be true

#### Scenario: Legacy records not yet re-swept
- **WHEN** a record predates this field and has not yet been re-touched by the incremental pass or metrics sweep
- **THEN** it SHALL carry no `has_weights` value (treated as null downstream), becoming populated as the sweep cycles

## MODIFIED Requirements

### Requirement: Schema compliance
Each JSONL record SHALL conform to the schema: `id`, `author`, `url`, `size_b`, `format`, `license`, `has_weights`, `tags`, `downloads`, `likes`, `created_at`, `modified_at`, `metrics_refreshed_at`. (Legacy records may carry a `quant` key in place of `format`/`license`; the builder SHALL drop `quant` and backfill the canonical columns during Parquet build. Records not yet re-swept may lack `has_weights`.)

#### Scenario: Record fields
- **WHEN** a model record is written to a `models-NNN.jsonl.gz` shard
- **THEN** the record SHALL include all schema fields, with `id` as `"author/model"`, `url` as the full `https://huggingface.co/...` URL, dates as ISO-8601 UTC strings, `has_weights` as a boolean, and numeric fields (`downloads`, `likes`) as integers
