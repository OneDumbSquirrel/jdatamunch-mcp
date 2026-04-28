# Changelog

## [1.0.0] — Phase A complete (V1 closure)

This release completes the Phase A roadmap that earns a stable 1.x.x. The full
plan and rationale lives in `todo.md`. Headline guarantees added in this release:

### Statistical correctness
- **Welford online mean + Neumaier-compensated sum** (A1) — replaces the naive
  `num_sum += num` accumulator. Mean stays accurate at 1e-9 relative error
  across 1e-6..1e6 mixed magnitudes.
- **t-digest streaming quantiles** (A2) — every numeric column now exposes
  `p01 / p25 / p50 / p75 / p95 / p99` in addition to min/max/mean/median, plus
  `std_dev` and `variance` from Welford. Bounded ~3 KB/column regardless of
  row count. Replaces the order-biased 10k reservoir.
- **HyperLogLog approximate cardinality** (A3) — once the 5,000-distinct
  exact-count cap is hit, columns now report `cardinality_approx` from a
  2,048-register HLL (~2% standard error). `cardinality_estimated: true` flags
  the difference.

### Schema intelligence
- **Semantic column types** (A6) — 13 detectors (`email`, `url`, `uuid`,
  `iso_currency`, `phone_e164`, `ipv4`, `ipv6`, `iso_country`, `lat`, `lon`,
  `zip_us`, `boolean_text`, `percentage`) populate `semantic_type` +
  `semantic_confidence` on each column profile.
- **Type-inference confidence + violation samples** (A7) — every column carries
  `type_confidence` (fraction of values matching the dominant type) and up to
  five `type_violation_samples` so agents can spot mixed-type columns.

### Crash safety
- **Atomic ingest** (A4) — `data.sqlite` is written to `data.sqlite.tmp` first
  and renamed only after profiles compute successfully. `index.json` gets a
  sidecar `index.json.sha256`. A `_lock` file marks in-progress runs;
  `index_local` auto-recovers from prior crashes by cleaning stale tmp files.
  WAL + `synchronous=NORMAL` replace the previous `synchronous=OFF`.
- **`validate_index` tool** (A5) — runs `PRAGMA integrity_check`, cross-checks
  row count and schema against `index.json`, verifies the checksum sidecar,
  and reports stale-lock state. Returns `overall_status: ok | warning | error`.

### Reproducibility & freshness
- **`get_dataset_history` tool + profile snapshots** (A8) — every successful
  `index_local` appends a compact snapshot (timestamp, source hash, schema
  digest) to `_history.jsonl`. Bounded to the last 50 snapshots. Use this to
  observe drift across re-ingests of the same dataset.
- **Deterministic random sampling** (A9) — `sample_rows` accepts a `seed`
  parameter (when `method='random'`) for reproducible selection.
- **Cross-parser normalization contract** (A10) — `parser/normalize.py`
  funnels all native-typed cells (JSONL / Parquet / Excel) through one path,
  guaranteeing CSV / JSONL / Parquet produce identical column profiles for
  the same logical data.

### Schema versioning
- **Index migration framework** (A11) — `INDEX_VERSION` bumped to 2. Indexes
  written under v1 are now upgraded in place via a registered migration
  rather than silently triggering a full re-index. Future bumps register a
  new migration in `storage/migrations.py`.

### Test infrastructure (A12)
- New test modules: `test_welford`, `test_tdigest`, `test_hll`,
  `test_semantic_types`, `test_crash_safety`, `test_validate_index`,
  `test_dataset_history`, `test_migrations`, `test_determinism`,
  `test_normalize`, `test_aggregate_correctness`. Test count: **266 passing**.

### Stability guarantees declared as of 1.0.0
- Profile fields documented above are part of the public on-disk schema.
- New fields will be added under additive migrations only.
- Crash semantics: a kill at any point during `index_local` leaves the
  dataset in one of two states — fully indexed or absent. Never partial.
- `validate_index` is the canonical recovery flow; if it returns `ok`, the
  dataset is consistent.

## [0.8.4] — 2026-04-15

### Documentation
- **Hermes Agent integration** — added "Works with" section to README with Hermes Agent config example; submitted optional skill PR to [NousResearch/hermes-agent#10413](https://github.com/NousResearch/hermes-agent/pull/10413)

## [0.8.3] — 2026-04-09

### New features

- **`meta_fields` support** — control which `_meta` fields appear in tool responses via `JDATAMUNCH_META_FIELDS` env var. Matches jcodemunch-mcp's `meta_fields` affordance. Values: unset/`[]` = strip `_meta` entirely (default, maximum token savings), `null`/`all`/`*` = include all fields, comma-separated list = include only those fields (e.g. `timing_ms,powered_by`).

### Tests

- 11 new tests for meta_fields config parsing and filtering (228 total, 10 skipped for optional deps)

## [0.8.2] — 2026-04-08

### Documentation

- **README.md rewrite** — added documentation index, file format table, all 18 tools organized by category (indexing, exploration, querying, analysis, management), semantic search, cross-dataset joins, correlations, NL summaries, data quality tools, built-in guardrails, full configuration reference
- **QUICKSTART.md** — new beginner-friendly guide: install, connect, index, query in three steps. Plain-English examples throughout.
- **USER-MANUAL.md** — comprehensive manual for non-developer users (analysts, finance, ops). Covers all 18 tools with plain-language explanations, real-world "ask your AI" examples, tips, best practices, and troubleshooting.

## [0.8.1] — 2026-04-08

### New features

- **`list_repos()` tool** — list GitHub repositories indexed via `index_repo`. Shows repo name, HEAD SHA (truncated to 12 chars), dataset count, total rows, total size, and dataset names for each repo.

### Tests

- 8 new tests (217 total, 10 skipped for optional deps)

## [0.8.0] — 2026-04-08

### New features

- **Semantic / embedding search** — `search_data` now supports `semantic=true` for embedding-based column search. Queries like "where did the crime happen" match `AREA NAME` even without keyword overlap. Three new parameters: `semantic` (enable), `semantic_weight` (blend ratio, default 0.5), `semantic_only` (skip keyword scoring). Lazily embeds columns on first semantic query; embeddings cached persistently in SQLite.
- **`embed_dataset(dataset)` tool** — precompute column embeddings for a dataset. Optional warm-up so the first `search_data` semantic query returns immediately. Supports `force=true` to recompute.
- **Three embedding providers** (first configured wins): sentence-transformers (local, free via `JDATAMUNCH_EMBED_MODEL`), Gemini (`GOOGLE_API_KEY` + `GOOGLE_EMBED_MODEL`), OpenAI (`OPENAI_API_KEY` + `OPENAI_EMBED_MODEL`). All imports are lazy — zero impact when semantic search is not used.
- **`[semantic]` optional dependency** — `pip install jdatamunch-mcp[semantic]` installs sentence-transformers

### Tests

- 32 new tests (209 total, 10 skipped for optional deps)

## [0.7.1] — 2026-04-08

### New features

- **`delete_dataset(dataset)` tool** — remove an indexed dataset and its SQLite store, freeing disk space. Returns rows/columns removed and bytes freed.
- **`join_datasets(dataset_a, dataset_b, join_column_a, join_column_b)` tool** — SQL JOIN across two indexed datasets via SQLite `ATTACH DATABASE`. Supports `inner`, `left`, `right`, and `cross` join types. Column projection (`columns_a`/`columns_b`), per-side filters (`filters_a`/`filters_b`), ordering, and pagination. Handles column-name collisions with `__b` suffix. Row limit capped at 500, 30 columns per side. Right joins emulated via table swap (SQLite limitation).

### Bug fixes

- Fixed unclosed SQLite connections in `create_table` and `create_indexes` that caused `PermissionError` on Windows when deleting datasets (WAL file locks)

### Tests

- 26 new tests (177 total, 10 skipped for optional deps)

## [0.6.0] — 2026-04-08

### New features

- **`get_correlations(dataset)` tool** — compute pairwise Pearson correlations between all numeric columns via SQLite. Returns pairs sorted by |r| descending with strength labels (`very strong`, `strong`, `moderate`, `weak`, `negligible`), direction, and pair counts. Configurable `min_abs_correlation` threshold (default 0.3), optional column filter, `top_n` cap (default 20, max 200). Caps at 50 numeric columns to avoid O(n^2) blowup.

### Tests

- 13 new tests (151 total, 10 skipped for optional deps)

## [0.5.0] — 2026-04-08

### New features

- **`index_repo(url)` tool** — index data files directly from a GitHub repository. Discovers CSV, Excel, Parquet, and JSONL files via the GitHub Trees API, downloads each to a temp directory, and indexes via the existing `index_local` pipeline. Datasets are named `{owner}--{repo}--{filename}`.
  - Incremental: caches HEAD SHA to skip entirely when repo is unchanged
  - Limits: 50 MB per file, 20 files per repo
  - Concurrent downloads (semaphore-limited to 5)
  - Supports `GITHUB_TOKEN` env var for private repos and rate limits

### Tests

- 18 new tests for index_repo (138 total, 10 skipped for optional deps)

## [0.4.0] — 2026-04-08

### New features

- **Natural-language summaries** — every `index_local` call now auto-generates a dataset-level summary and per-column summaries from profiled statistics. Summaries describe data shape, types, ranges, cardinality, quality issues, and temporal spans — no external API calls needed.
- **`summarize_dataset(dataset)` tool** — regenerate summaries for an already-indexed dataset without re-parsing the source file. Useful after schema or profile changes.

### Improvements

- `describe_dataset` now includes `dataset_summary` and per-column `ai_summary` fields in responses
- Column summaries surface cardinality labels (unique identifier, categorical, binary, constant, etc.), null-rate warnings, and value previews for low-cardinality columns

### Tests

- 18 new tests (120 total, 10 skipped for optional deps)

## [0.3.0] — 2026-04-01

### New tools

- **`get_schema_drift(dataset_a, dataset_b)`** — compare schema metadata between two indexed datasets: detects added/removed columns, type changes, and null-rate shifts (≥1% delta). Assessment: `identical` | `additive` | `breaking`. Pure in-memory comparison of indexed profiles — no re-reading source files.
- **`get_data_hotspots(dataset, top_n=10)`** — rank columns by composite data-quality risk combining null rate, cardinality anomalies, and numeric outlier spread (coefficient of variation). Per-column `assessment: low|medium|high`. Top-N capped at 50. Analogous to jcodemunch's `get_hotspots`.

### Tests

- 23 new tests (91 total, 1 skipped for optional deps)

## [0.2.1] — 2026-03-31

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)

## [0.2.0] — 2026-03-31

### New features

- **Parquet support** — `.parquet` files indexed and queried via `pyarrow`
- **JSONL/NDJSON support** — `.jsonl` and `.ndjson` files parsed line-by-line; schema inferred from first N rows
- **Token budget enforcement** (`budget.py`) — every tool response is capped at a configurable token limit (`JDATAMUNCH_MAX_RESPONSE_TOKENS`, default 8 000); falls back to generic list-field trimming when needed
- **Anti-loop call tracker** (`call_tracker.py`) — detects and warns when an LLM agent is paginating through a dataset row-by-row in a tight loop
- **Wide-table pagination** — `describe_dataset` auto-paginates at 60 columns; new `columns_offset` parameter lets callers page through remaining columns

### Improvements

- Hard caps added for all tool parameters: `top_n` ≤ 200, `histogram_bins` ≤ 50, `search_data` max_results ≤ 50, `aggregate` limit ≤ 1 000
- `get_rows` / `sample_rows` auto-project to 30 columns on wide tables; caller can override with explicit `columns` list
- `describe_dataset` tool description updated to document pagination behaviour
- `describe_column` and `search_data` tool descriptions document their caps
- Improved test fixtures (`tests/conftest.py`)

### Housekeeping

- Added `LICENSE` file (dual-use: free for non-commercial, paid for commercial)
- `index_local` description updated to list all supported formats

## [0.1.2] — 2026-03-27

### Performance

- Bulk SQLite insert, string fast-path, corrected `is_unique` detection for high-cardinality columns

## [0.1.1] — 2026-03-26

### Bug fixes

- Fixed token cost calculations in benchmark results (were off by 1 000×)

## [0.1.0] — 2026-03-25

### Initial release

- CSV and Excel (.xlsx/.xls) indexing via SQLite
- Tools: `index_local`, `list_datasets`, `describe_dataset`, `describe_column`, `search_data`, `get_rows`, `sample_rows`, `aggregate`, `get_session_stats`
- jMRI-Full compliant
