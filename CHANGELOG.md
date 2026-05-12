# Changelog

## [1.9.0] — 2026-05-12 — `get_schema_impact` (Phase-1 COMPLETE)

Fourth and final Phase-1 sibling-parity tool. Walks the inferred FK
graph to surface transitive impact of a column-level schema change.
Inspired by jcodemunch-mcp's `get_blast_radius`, ported to jData's
FK-graph + runtime-traffic shape.

### New: `get_schema_impact` MCP tool

Three change kinds:

- **`drop_column`** (default) — surface every dataset / runtime query
  that *might* reference this column.
- **`rename_column`** — same surfaces; `recommended_action` references
  `new_name` for cascade planning.
- **`retype_column`** — additionally checks `new_type` compatibility
  against each FK-related column's type. Cross-family changes (e.g.
  `integer` → `string`) surface in `summary.type_mismatches`.

### Output

- `direct_impact` (depth 1) — fk_source, fk_target,
  cross_dataset_name_match, runtime_traffic entries.
- `transitive_impact` (depth ≥ 2) — BFS through the FK graph,
  capped at `_MAX_IMPACT_ITEMS = 50`.
- `summary` — `datasets_affected`, `fk_edges_broken`,
  `runtime_calls_in_window`, `type_mismatches`,
  `cross_dataset_name_matches`.
- `blast_score` ∈ [0, 1] — soft-normalised against index size so a
  5-edge impact in a 50-dataset warehouse scores higher than the same
  5 in a 500-dataset one.
- `recommended_action` — verb tracks the change kind ("drop" / "rename
  to X" / "retype to Y").

### Stats

- Tool count: 30 → 31
- Tests: 418 → 434 (+16 new)

### Phase-1 sibling-parity batch complete

| Tool | Version |
|---|---|
| `ingest_sql_log` (foundational runtime primitive) | v1.6.0 |
| `find_unused_columns` | v1.7.0 |
| `check_column_drop_safe` (killer feature) | v1.8.0 |
| `get_schema_impact` | v1.9.0 |

Phase 2 (deferred until user signal): `data_health_radar`,
`find_similar_columns`, `data_pr_risk_profile`, `get_redaction_log`.

Inspired by `get_blast_radius` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.3).

## [1.8.0] — 2026-05-12 — `check_column_drop_safe` (Phase-1 #3 — killer feature)

The killer feature of the Phase-1 sibling-parity batch. Composite
preflight that fuses four channels — PK status, FK heuristics, cross-
dataset name match, and runtime traffic — into a single verdict plus
ranked blockers and a one-line `recommended_action`.

### New: `check_column_drop_safe` MCP tool

Verdict tiers (highest-severity-first):

- **`pk_blocking`** — column is a primary-key candidate
- **`fk_blocking`** — likely foreign-key participation (source or target)
- **`runtime_observed`** — `runtime_query_calls` in last 30 days (window configurable)
- **`cross_dataset_blocking`** — another indexed dataset has a same-named column
- **`safe_to_drop`** — none of the above

### Channels

1. **PK status** — `is_primary_key_candidate` from the static profile.
2. **FK source** — heuristic name-match (`user_id` → dataset `users` with PK `id`) plus direct PK name-match across other indexed datasets. Cheap structural check; no value-containment scan.
3. **FK target** — mirror of #2: this column is a PK and other datasets carry plausible FK-shaped columns (`<self>_id` / `<singular>_id`).
4. **Runtime traffic** — sum of `calls` in `runtime_query_calls` over `window_days` (default 30).
5. **Cross-dataset name match** — case-insensitive same-name lookup across `list_datasets()`. Capped at 10 hits.

### Honest hint when runtime data is absent

When no `ingest_sql_log` has run against the dataset, `safe_to_drop`
verdicts carry an explicit caveat in `recommended_action` pointing the
operator at `ingest_sql_log`. The static channels alone can prove
*risk*, but not *safety*.

### Stats

- Tool count: 29 → 30
- Tests: 406 → 418 (+12 new)

Inspired by `check_delete_safe` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.2).

## [1.7.0] — 2026-05-12 — `find_unused_columns` (Phase-1 #2)

Second Phase-1 tool from the sibling-parity PRD. The first consumer of
the `runtime_query_calls` table populated by `ingest_sql_log` (v1.6.0).
Answers: *which columns in this dataset have no recent query traffic?*

### New: `find_unused_columns` MCP tool

Surfaces columns with zero or stale runtime reads over a configurable
window. Three reason classifications:

- **`zero_hits`** — column never appeared in any query, in or out of window
- **`stale`** — column has appeared at some point, but never within the requested window
- **`below_min_calls`** — column has hits in window but fewer than `min_calls`

### Refusal-by-design

When the dataset has zero rows in `runtime_query_calls`, the tool
**refuses** with an explicit `refused_no_runtime_data` error rather
than silently flagging every column. The hint directs the operator at
`ingest_sql_log`. Mirrors the same guard in jcodemunch-mcp's
`find_unused_paths`.

### Defaults

- **`exclude_pk`** (default true) — skips columns flagged as
  `is_primary_key_candidate` by the static profiler. PKs are almost
  always read by JOINs but may not always surface in extracted column
  tokens.
- **`exclude_audit`** (default true) — skips `created_at`,
  `updated_at`, `_dbt_*`, `etl_*`, and other scaffolding patterns.
- **`window_days=30`**, **`min_calls=0`** — single observed call counts
  as used.

### Stats

- Tool count: 28 → 29
- Tests: 392 → 406 (+14 new)

Inspired by `find_unused_paths` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.4).

## [1.6.0] — 2026-05-12 — Runtime SQL-log ingest (Phase-1 sibling-parity foundation)

First Phase-1 deliverable from the sibling-parity PRD. Adds the
foundational runtime-traffic primitive that downstream tools
(`find_unused_columns`, `check_column_drop_safe`, `data_health_radar`)
will read from. Inspired by jcodemunch-mcp's `runtime/` pipeline but
written fresh against jData's per-dataset SQLite shape.

### New: `ingest_sql_log` MCP tool

Ingests a SQL log file (pg_stat_statements CSV or generic JSON-Lines,
`.gz` transparent) into the per-dataset runtime tables. Each query is:

1. **Parsed** — table + column refs extracted via regex over SELECT /
   WHERE / ON / GROUP BY / ORDER BY / HAVING clauses. Schema-qualified
   names and quoted identifiers (double-quote, backtick, bracket) all
   normalise to the trailing identifier.
2. **Redacted** at the chokepoint — string literals → `'?'`, numeric
   literals → `?`, plus the cell-PII registry on any residual text.
   `redact=False` opt-out for synthetic data only.
3. **Resolved** — for each (table, column) tuple, find the indexed
   dataset whose name matches the table (case-insensitive, exact). Over-
   emitted column tokens that aren't in the dataset's schema drop out.
4. **Upserted** — `ON CONFLICT(query_fingerprint, table_ref,
   column_ref, source)` accumulates `calls` and `total_time_ms` and
   refreshes `last_seen`. Per-pattern redaction counts persist to
   `runtime_redaction_log` so operators can verify the chokepoint
   actually fires on production traffic.

Unmapped queries (tables that don't match any indexed dataset) count
toward the response's `unmapped_queries` but aren't persisted.

### New: `redact_sql_query_text` and `redact_trace_message` public helpers

Trace-level extensions of the cell-PII redaction module shipped in
v1.5.0:

- `redact_sql_query_text(query, ...)` — strips string + numeric literals
  (so query fingerprints survive but values don't), then applies the
  cell registry. `credit_card` is off by default for SQL text — Luhn-
  valid 13–19 digit sequences inside arbitrary tokens are nearly always
  false positives once literals are scrubbed.
- `redact_trace_message(text, ...)` — IPv4 sweep plus the cell registry,
  for free-form trace / log message bodies.

### Schema migration

`INDEX_VERSION` bumped 2 → 3. The migration is **additive only** — no
profile recompute, no forced reindex. Legacy v2 indexes gain empty
runtime tables on first `ingest_sql_log` call.

### What's NOT in this release

The dependent tools (`find_unused_columns`, `check_column_drop_safe`,
`get_schema_impact`) ship in the **next** Phase-1 batch — they need
`ingest_sql_log` to bake first.

### Stats

- Tool count: 27 → 28
- Tests: 351 → 392 (+41 new across redact + parser + ingest)
- New module: `jdatamunch_mcp/runtime/` (sql_log, ingest, tables)

Inspired by `import_runtime_signal` in jcodemunch-mcp (see
`C:/MCPs/PRD_sibling_parity_v1.md` §5.1).

## [1.5.0] — Cell-level redaction on the output side

Tabular tools now scrub PII and credentials from cells before returning
them to MCP clients. CSV / Excel / Parquet / JSONL data routinely carry
emails, SSNs, credit-card numbers, API keys, and PEM bodies in raw
columns — those cells would otherwise flow straight into LLM context
where they may be cached, logged, or reflected to a tool downstream.
The default policy is ON; callers opt out per call.

### New
- **`src/jdatamunch_mcp/redact.py`** — single-chokepoint redaction module.
  Built-in patterns: `email`, `ssn` (SSA-rule validated), `credit_card`
  (Luhn-checked post-match), `jwt`, `private_key` (full PEM blocks),
  `aws_access_key`, `github_pat`, `slack_token`, `api_key_prefixed`
  (Stripe `sk_live_…` / `sk_test_…` / `rk_…`), `api_key_openai` (`sk-…`).
  Numeric cells are never scrubbed — agents rarely treat numbers as PII.
- **`redact`, `redact_patterns`, `redact_skip_columns`** params on
  `get_rows`, `sample_rows`, `run_sql`, `aggregate`, and `describe_column`.
  `redact=True` by default. `redact_patterns` layers additional Python
  regex onto the built-in set; invalid patterns are silently skipped and
  surfaced via `_meta.redaction.invalid_custom_patterns`.
  `redact_skip_columns` exempts named columns (e.g. an `email_hashed`
  column where the email pattern would false-positive).
- **`_meta.redaction`** block on every wired tool response —
  `{"applied": bool, "cells_redacted": int, "patterns_matched": {kind: count}}`.
  Surfaced even when `applied=False` so the absence of redaction is
  auditable from the wire.
- **34 new tests** (`test_redact.py` + `test_redaction_e2e.py`).
  351 passed, 1 skipped — fully backward-compatible.

### Notes
- `aggregate` caches the raw, un-redacted result; the redaction policy
  is enforced at read time so flipping `redact=False` on a cache hit
  still returns raw cells.
- `describe_column` redacts `value_distribution`, `top_values`, and
  `sample_values`. Numeric stats (min / max / mean / median / histogram)
  are never altered.
- `search_data` is deliberately not wired — the user is explicitly
  searching, so redacting matches would defeat the search.

---

## [1.4.0] — Phase C (optional post-V1 polish)

Closes the Phase C list in `todo.md`. 317 tests passing. Fully backward-compatible.

### Aggregation
- **`aggregate(approximate=True)`** (C1) — new approximate-mode path. Routes
  `count_distinct` → HyperLogLog (~2% standard error), `median` → t-digest
  (~1% accuracy at extreme quantiles), `sum`/`avg` → sampled estimator with
  95% confidence-interval half-width reported in `result.confidence`.
  Whole-dataset only (no group_by/having/order_by). Useful for very large
  joined datasets where exact aggregations are expensive.

### Index metadata
- **Dataset content fingerprint** (C2) — `index.json` now carries
  `fingerprint = sha256(sorted(column_names) + first_1000_row_hash)`.
  Independent of filename / path: two physically distinct files with
  identical logical content share the same fingerprint. Surfaced in
  `list_datasets`.
- **Per-dataset learned null tokens** (C3) — new `profiler/null_learner.py`
  scans completed profiles for sentinel-looking tokens that recur across
  multiple columns at non-trivial frequency (e.g. `TBD`, `999`, `----`,
  `UNKNOWN`). Surfaced as `index.learned_null_tokens` so agents can decide
  whether to treat them as nulls in downstream filters. Informational only —
  profiling behavior is unchanged.

### Summarization
- **Coarse domain classification** (C4) — `summarize_dataset` now appends a
  `Likely domain: …` line when evidence supports it: `geo`, `financial`,
  `log`, `event`, or `temporal`. Driven by column-name tokens + semantic
  types. Conservative — emits nothing when evidence is weak.

### Telemetry
- **Per-tool token-savings attribution** (C5) — `_savings.json` now records
  `per_tool[<tool>] = {tokens_saved, calls}`. Surfaced via
  `get_session_stats.result.per_tool` sorted by tokens saved descending.
  Lets you see which tools contribute most to the savings number.

### Cache
- **Cross-session aggregate cache** (C6) — formalized: the result cache
  shipped in 1.1.0 (`storage/result_cache.py`) already persists across
  sessions as JSON files under `~/.data-index/{dataset}/_cache/`, keyed on
  `(tool, source_hash, normalized_args)`. Re-indexing invalidates.

### Migrations
- **v1 → v2 migration extended** to populate `fingerprint` (None) and
  `learned_null_tokens` ([]) on legacy indexes. Idempotent. No behavior
  change for indexes already at v2.

### Tests
- 16 new tests across `test_fingerprint`, `test_per_tool_savings`,
  `test_domain_classification`, `test_null_learner`,
  `test_approximate_aggregate`. Total: **317 passing**.

## [1.1.0] — Phase B (recommended polish)

Adds the eight Phase-B items from `todo.md`. 301 tests passing. Fully
backward-compatible — every new capability is additive.

### New tools (B1, B3, B4, B5, B8)
- **`run_sql`** — read-only sandboxed SQL escape hatch. Accepts a single
  `SELECT` (or `WITH … SELECT`) over one or more datasets, ATTACHed under
  schema names. `PRAGMA query_only=1`, 10 s budget, 500-row cap, forbidden-
  keyword guard. The supported way to express HAVING / window functions /
  CTEs / multi-way joins that the structured tools don't cover.
- **`plan_query`** — natural-language intent → ranked tool-call sequence.
  Pure routing; no LLM call. Built-in intents: summarize, anomalies,
  compare, join, filter, trend, correlate.
- **`get_dataset_health`** — composite quality grade (A–F) combining null
  severity, type-confidence, constant-column count, primary-key presence,
  semantic-typing coverage, and drift history.
- **`suggest_keys`** — ranks primary-key candidates with confidence scores
  and reasons (integer column, UUID format, no nulls, exact-count unique).
- **`suggest_joins`** — discovers FK candidates by sampling 500 distinct
  values from each non-PK column and scanning up to 20 other indexed
  datasets' PK candidates for ≥ 95% containment.
- **`get_distribution`** — unified bin-counts: numeric → equal-width bins,
  datetime → time-bucket bins, categorical → top-n + 'other'.

### Existing-tool extensions
- **`aggregate(having=[…])`** (B11) — post-aggregation filters on aggregation
  aliases. Supports eq/neq/gt/gte/lt/lte/in/between/is_null. Substitutes
  the aggregate expression into HAVING so it works even when an alias
  collides with a source column name.
- **`get_correlations(method='pearson'|'spearman')`** (B10) — Spearman
  uses rank-transformed values via SQL window functions, robust to
  outliers and monotonic non-linear relationships.
- **`search_data`** (B9) — keyword scoring upgraded to BM25 in the default
  `all` scope. Documents include column name + ai_summary + value index +
  semantic_type. Existing schema-only and values-only paths preserved.
- **`index_local(depth='shallow'|'standard'|'deep')`** (B7) — shallow caps
  profiling at 100k rows for fast first-look; deep additionally pre-warms
  the correlation cache.

### Performance / infrastructure
- **Aggregate result cache** (B2) — `aggregate`, `get_correlations`, and
  `get_data_hotspots` cache results under `~/.data-index/{dataset}/_cache/`
  keyed on `(tool, source_hash, normalized_args)`. Invalidated on every
  re-index. `_meta.cache_hit` reports hit/miss.
- **Parquet schema pushdown** (B6) — Parquet parser now exposes per-column
  logical types via `metadata['column_types']`. `index_local` skips the
  10k-row sample-based type inference when the source already carries
  authoritative type metadata.
- **MEMORY journal during ingest** — bulk-load uses `PRAGMA
  journal_mode=MEMORY` instead of WAL. The tmp file is disposable on crash
  (A4 invariant), so no on-disk journal is needed; this also clears the
  Windows rename race that prior WAL sidecars caused.

### Tests
- 35 new tests across `test_having`, `test_spearman`, `test_bm25`,
  `test_health_keys_joins`, `test_distribution`, `test_plan_query`,
  `test_aggregate_cache`, `test_run_sql`, `test_depth`. Total: **301 passing**.

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
