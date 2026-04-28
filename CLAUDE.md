# jdatamunch-mcp — Project Brief

## Current State
- **Version:** 1.0.0 (Phase A complete — see `todo.md` and CHANGELOG `[1.0.0]`)
- **GitHub:** `jgravelle/jdatamunch-mcp`
- **Python:** >=3.10
- **Index format:** INDEX_VERSION = 2 (v1 → v2 migration registered in `storage/migrations.py`)
- **Tool count:** 21 (added `validate_index`, `get_dataset_history` in 1.0.0)

## Key Files
```
src/jdatamunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher
  config.py                    # Index path, max rows env vars
  security.py                  # Path validation
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
    get_data_hotspots.py       # get_data_hotspots: rank columns by data-quality risk (null, cardinality, outlier)
    delete_dataset.py          # delete_dataset: remove indexed dataset and SQLite store
    embed_dataset.py           # embed_dataset: precompute column embeddings for semantic search
    get_correlations.py        # get_correlations: pairwise Pearson correlations between numeric columns
    join_datasets.py           # join_datasets: cross-dataset SQL JOIN via ATTACH DATABASE
    summarize_dataset.py       # summarize_dataset: regenerate NL summaries for indexed dataset
    index_repo.py              # index_repo: index data files from a GitHub repository
    list_repos.py              # list_repos: list GitHub repositories indexed via index_repo
    validate_index.py          # validate_index: integrity check on dataset (1.0.0)
    get_dataset_history.py     # get_dataset_history: profile snapshots over time (1.0.0)
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
