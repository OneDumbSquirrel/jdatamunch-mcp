# jdatamunch-mcp — Project Brief

## Current State
- **Version:** 1.12.1 (Hygiene patch. `__version__` now derived from `importlib.metadata.version("jdatamunch-mcp")` in `__init__.py` — pyproject.toml is the single source of truth, the runtime/packaging version strings can no longer disagree. Mirrors jcm's pattern. Backstory: v1.12.0 shipped with the hardcoded literal stuck at 1.9.0, three minors stale.)
- **Version (v1.12.0):** Phase-2 jData COMPLETE — `find_similar_columns`: multi-signal cross-dataset column consolidation. Fuses name (token Jaccard, snake+camel aware) + type + top-value Jaccard + cardinality + embedding cosine (when present). Union-find clustering; verdict tiers: near_duplicate / naming_drift / parallel_definition / overlapping_topic. Differs_by breakdown per pair makes verdict auditable. Mirrors jcm's find_similar_symbols.
- **GitHub:** `jgravelle/jdatamunch-mcp`
- **Python:** >=3.10
- **Index format:** INDEX_VERSION = 3 (v1→v2→v3 migrations registered in `storage/migrations.py`; v3 is additive — new runtime tables created on first ingest, legacy v2 indexes load fine)
- **Tool count:** 35 (1.6.0 added `ingest_sql_log`; 1.7.0 added `find_unused_columns`; 1.8.0 added `check_column_drop_safe`; 1.9.0 added `get_schema_impact`; 1.10.0 added `get_redaction_log` + `get_data_hotspots` v2; 1.11.0 added `data_health_radar` + `diff_data_health_radar`; 1.12.0 adds `find_similar_columns`)
- **Tests:** 470 passed, 10 skipped (1.12.0)

## Key Files
```
src/jdatamunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher
  config.py                    # Index path, max rows env vars
  security.py                  # Path validation
  redact.py                    # (1.5.0) Cell-level redaction. Built-in patterns: email, ssn (SSA-rule), credit_card (Luhn-checked), jwt, private_key (PEM blocks), aws_access_key, github_pat, slack_token, api_key_prefixed (Stripe), api_key_openai. Public API: redact_rows / redact_value_distribution / redact_scalar_list / merge_summary / redaction_meta. Wired into get_rows/sample_rows/run_sql/aggregate/describe_column with redact=True default and `_meta.redaction` audit block on every response. Numeric cells are never scrubbed. (1.6.0) Adds redact_sql_query_text (strips string + numeric literals, applies cell registry) and redact_trace_message (IPv4 + cell registry) for the runtime ingest chokepoint.
  runtime/                     # (1.6.0) Phase-1 runtime traffic ingest. sql_log.py = pg_stat_statements CSV + generic JSONL parser (.gz transparent), extracts table + column refs via regex. ingest.py = orchestrator (parse → redact → resolve → upsert) — per-dataset SQLite, ON CONFLICT accumulates calls + total_time. tables.py = runtime_query_calls + runtime_redaction_log schemas + idempotent ensure_runtime_tables.
  embeddings.py                # Provider detection (sentence-transformers/Gemini/OpenAI), embed_texts(), cosine_similarity()
  parser/
    normalize.py               # Cross-parser native→string normalization (1.0.0)
  profiler/
    column_profiler.py         # Per-column type inference, Welford+Neumaier stats, finalize_profile()
    tdigest.py                 # Streaming quantile estimator (1.0.0)
    hll.py                     # HyperLogLog approximate cardinality (1.0.0)
    semantic_types.py          # Semantic column-type detectors (1.0.0)
  storage/
    data_store.py              # DataStore: load/save DatasetIndex (index.json) + crash-safety helpers
    migrations.py              # INDEX_VERSION migration registry (1.0.0)
    sqlite_store.py            # SQLite backend: create_table, insert_batch, create_indexes
    embedding_store.py         # ColumnEmbeddingStore: column embedding CRUD in dataset SQLite
    token_tracker.py           # estimate_savings, record_savings, cost_avoided
  tools/
    index_local.py             # Index a local CSV/Excel file (single-pass profiling + SQLite load)
    list_datasets.py           # List indexed datasets
    describe_dataset.py        # Full schema profile (primary orientation tool)
    describe_column.py         # Deep stats for one column
    sample_rows.py             # Sample rows (optionally filtered)
    get_rows.py                # Rows by index range or filter
    search_data.py             # Search rows by column value / pattern
    aggregate.py               # Aggregate (count/sum/mean/min/max) with optional groupby
    get_session_stats.py       # Session token savings stats
    get_schema_drift.py        # get_schema_drift: compare schema between two datasets (added/removed/type/nullability)
    get_data_hotspots.py       # get_data_hotspots: rank columns by data-quality risk. v1: null + cardinality + outlier. v2 (1.10.0): adds traffic signal from runtime_query_calls when present (weights null=0.30, card=0.20, outlier=0.20, traffic=0.30); honest-hint caveat in _meta.runtime_caveat when include_runtime=True but no traces ingested; v1 scoring preserved when traces absent or include_runtime=False.
    delete_dataset.py          # delete_dataset: remove indexed dataset and SQLite store
    embed_dataset.py           # embed_dataset: precompute column embeddings for semantic search
    get_correlations.py        # get_correlations: pairwise Pearson correlations between numeric columns
    join_datasets.py           # join_datasets: cross-dataset SQL JOIN via ATTACH DATABASE
    summarize_dataset.py       # summarize_dataset: regenerate NL summaries for indexed dataset
    index_repo.py              # index_repo: index data files from a GitHub repository
    list_repos.py              # list_repos: list GitHub repositories indexed via index_repo
    validate_index.py          # validate_index: integrity check on dataset (1.0.0)
    get_dataset_history.py     # get_dataset_history: profile snapshots over time (1.0.0)
    find_unused_columns.py     # (1.7.0) Runtime-driven dead-column detection. Reads runtime_query_calls + dataset schema; surfaces columns with reason ∈ {zero_hits, stale, below_min_calls}. Refuses when no runtime data exists. PK + audit-field exclusion on by default. Audit patterns: created_at, updated_at, _dbt_*, etl_*, etc.
    check_column_drop_safe.py  # (1.8.0) Composite preflight: is this column safe to drop? Fuses PK status + FK heuristics (name-match + stem-match like `user_id` → `users.id`) + cross-dataset name match + runtime_query_calls in window. Verdict tiers: pk_blocking, fk_blocking, runtime_observed, cross_dataset_blocking, safe_to_drop. Ranked blockers (≤5) + recommended_action. Mirrors jcm's check_delete_safe.
    get_schema_impact.py       # (1.9.0) Transitive impact of a column-level schema change (drop_column / rename_column / retype_column). Walks the inferred FK graph to max_depth, classifies each hit as fk_source / fk_target / cross_dataset_name_match / runtime_traffic. For retype_column, flags type_mismatch entries at FK edges whose partner type wouldn't survive. Returns direct_impact + transitive_impact + summary + normalised blast_score ∈ [0, 1]. Mirrors jcm's get_blast_radius.
    health_radar.py            # (1.11.0) Pure-function core: compute_radar() builds the six (or seven) axis payload from raw signals; diff_radar() computes axis-by-axis deltas + verdict; diff_data_health_radar() is the MCP entry point that validates the input shape. No I/O.
    find_similar_columns.py    # (1.12.0) Multi-signal cross-dataset column similarity. Token Jaccard (snake+camel split) + type + top_values Jaccard + cardinality ratio + embedding cosine (when present). Embedding-aware weights (emb=0.50,name=0.20,value=0.15,type=0.10,card=0.05) vs lexical-only (name=0.45,value=0.30,type=0.15,card=0.10). Union-find clusters. Verdict tiers: near_duplicate / naming_drift / parallel_definition / overlapping_topic. differs_by breakdown per pair. Mirrors jcm's find_similar_symbols.
    data_health_radar.py       # (1.11.0) Six-axis dataset health radar: null_health, type_confidence, cardinality_health, pk_presence, semantic_coverage, schema_stability (+ optional runtime_coverage). Reads index.json + history + runtime_query_calls; produces a 0-100 score per axis + composite + A-F grade. Omitted axes never silently penalise the composite — they appear in `omitted_axes`.
    get_redaction_log.py       # (1.10.0) Forensic accounting of PII redactions for a dataset. Reads runtime_redaction_log (populated by ingest_sql_log with redact=True) and returns per-pattern counts + sources + last_seen. Filters by source ('sql_log') and since_days. Empty result is not an error — distinguished from unknown-dataset / invalid-source via structured `reason` codes. Mirrors jcm's get_redaction_log keyed on dataset_id.
```

## Architecture Notes
- `index_local` does a two-phase single pass: type inference on first 10k rows,
  then full pass for profiling + SQLite load.
- `describe_dataset` returns schema + stats from `index.json` (no SQLite query needed).
- `describe_column` returns deeper stats including full top-value distribution.
- Token tracker uses byte approximation (`raw_bytes / 4`) for zero-dependency speed.

## Benchmarks
Real production dataset (LAPD crime records, 1M rows):

| Dataset | Rows | Cols | File Size | Avg Ratio |
|---------|-----:|-----:|----------:|----------:|
| crime.csv | 1,004,894 | 28 | 255.5 MB | **25,333x** |

Baseline = full raw CSV tokenized. jDataMunch = `describe_dataset` + `describe_column`.
Benchmark harness: `python benchmarks/harness/run_benchmark.py <file.csv>`
