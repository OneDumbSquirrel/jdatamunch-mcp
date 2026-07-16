# jdatamunch-mcp — Project Brief

## Current State
- **Version:** 1.19.1 (docs only. Documentation wording only; no code, wire, or behavior change from 1.19.0.)
- **Version:** 1.19.0 (update model price constants to current Anthropic pricing. Anthropic has reduced input pricing across the Opus line since these models launched. `storage/token_tracker.PRICING` (feeds `cost_avoided()`, emitted in `_meta` on nearly every tool response: describe_dataset/get_rows/sample_rows/aggregate/join_datasets/get_session_stats/describe_column/get_data_hotspots) now tracks the current published rates: Opus $5/MTok, Sonnet $3/MTok, Haiku $1/MTok. Set `claude_opus` to $5; additively added `claude_sonnet` ($3) + `claude_haiku` ($1) for the full current model set (parity with jcm). `claude_opus` + `gpt5_latest` keys unchanged in name, so 1.x wire-compatible (additive keys). Token savings measured in tokens, valued at the applicable model rate — underlying savings unchanged, only the constants track current pricing. Does not touch the public token counter (tokens stored, valued at display time). No INDEX_VERSION bump, no tool add/rename. Tests: token/cost/session_stats/storage/aggregate suites green (27). Suite parity: jcm v1.108.130 (receipt) + jdoc v1.97.0 (same constants). **Framing note: tracks a vendor price REDUCTION, NOT a correction of an inflated figure — never describe as "overstating."**)
- **Version:** 1.18.0 (suite-parity retrieval verdict — Phase 3 of the suite-wide `_meta.verdict` work (jcm v1.108.116/.117 for code, jdoc v1.95.0 for docs). `search_data` now carries `_meta.verdict`: an empty column search is positive, token-saving evidence. States `ok` / `absent` / `degraded` — NO `low_confidence` (jData scores are rank-normalized, top always 1.0, so there's no calibrated confidence signal to threshold; fabricating one would be dishonest). `degraded` = semantic requested but the embedding channel fell back to keyword-only (precedence over `absent`); `absent` carries a `did_you_mean` list of column names containing a query term. Clean-room jData impl in new top-level `verdict.py` (`build_verdict` / `suggest_columns`); wire shape shared with siblings, no cross-suite import. Additive/1.x, no `INDEX_VERSION` bump, inline compute. Tests `tests/test_v1_18_0.py` (9).)
- **Version:** 1.17.0 (MCP `readOnlyHint` annotations — suite parity with jcodemunch PR #361 + jdoc. Every tool now advertises `ToolAnnotations(readOnlyHint=...)` at the `list_tools` chokepoint (`_apply_readonly_annotations`, non-mutating `model_copy`) so Claude Code plan mode runs jData's query tools silently while still prompting on the write-set. `_NON_READONLY_TOOLS` = index_local, index_repo, summarize_dataset, delete_dataset, embed_dataset, ingest_sql_log, tune_weights, check_embedding_drift — any tool that can mutate persistent state under ANY arg (conservative bias: mislabeling a writer read-only is the harmful direction; run_sql is SELECT-only so it's read). Additive/1.x-compatible (new tools/list field). Tests `tests/test_v1_17_0.py` (4). **Note: `check_embedding_drift` is marked False here (force=true writes the canary); jcm marks it True — jcm is the mild outlier, align later if it matters.**)
- **Version:** 1.16.0 (`analyze_perf`: per-tool latency + cache-hit telemetry. Completes the parity trio (jcm/jdoc both ship it). jData had NO latency telemetry + an uninstrumented cache; added both. New `perf.py` = in-memory per-tool latency ring (always on, 500/tool) + optional SQLite sink at `<index_path>/perf_telemetry.db` gated by `JDATAMUNCH_PERF_TELEMETRY=1` (FIFO cap `JDATAMUNCH_PERF_TELEMETRY_MAX_ROWS`, default 100k); `server.py` `call_tool` records each dispatch's wall-clock + ok flag (best-effort, never breaks a call). `storage/result_cache.py` gained per-tool hit/miss counters (`cache_stats()`) + `get()` takes optional `tool` (aggregate/get_correlations/get_data_hotspots pass it). New `analyze_perf` tool: `window=session` reads the ring, `1h|24h|7d|all` reads the sink; returns p50/p95/max/error_rate, slowest-by-p95, cache hit rates. Tool count 37 -> 38. **jData now has full agent-facing tool parity with its siblings — parity trio COMPLETE.** Tests `tests/test_v1_16_0.py` (12).)
- **Version:** 1.15.0 (`check_embedding_drift`: catches silent embedding-provider drift. Column embeddings power semantic `search_data` + `find_similar_columns`; if the provider model changes underneath a stored index (revision bump / reweight / swapped local model), saved vectors stop matching the live encoder and semantic ranking quietly degrades. New `embed_drift.py` pins a 16-string canary (data/column-flavored strings, not code tokens) in `<index_path>/embed_canary.json` and recomputes it on demand; `force=true` re-pins, otherwise reports max/mean cosine drift + per-canary, `alarm` when worst > `threshold` (default 0.05). Reuses jData's own `embeddings.detect_provider`/`embed_texts`/`cosine_similarity`. Closes the 2nd of the parity trio (jcm/jdoc both ship it). Tool count 36 -> 37. Tests `tests/test_v1_15_0.py` (9). Remaining parity gap: `analyze_perf`.)
- **Version:** 1.14.0 (`tune_weights`: the `search_data` ranking weight vector (name/value/type match weights plus the BM25 and semantic blend scales), previously hardcoded module constants, is now tunable and persistable per-dataset and honored at query time. Inspect / set / reset; overrides validated + clamped and stored in `<index_path>/ranking_tuning.json` (per-dataset wins over global wins over defaults). Closes the sibling-parity gap, since jcm and jdoc both ship `tune_weights`. Honest divergence: jData keeps no ranking-events ledger (`call_tracker` is ephemeral loop-detection), so tuning is explicit, not learned. New `tuning.py` owns the weight defaults (`DEFAULT_WEIGHTS`) + persistence; `search_data` resolves weights via `load_effective_weights` and honors a tuned `default_semantic_weight` when the caller omits `semantic_weight`. Tool count 35 -> 36. Tests `tests/test_v1_14_0.py` (14). Backstory: 2026-06-16 jData audit. v1.13.0/1.13.1 were docs-only, savings-meter disclosure + pricing sync.)
- **Version:** 1.12.2 (Adds `jdatamunch_guide` sibling-parity tool. Returns the version-current CLAUDE.md / AGENT.md policy snippet for jdatamunch-mcp so an agent can keep a one-line CLAUDE.md `"Call jdatamunch_guide and strictly follow its instructions."` instead of pasting a static block that drifts. Mirrors jcm's `jcodemunch_guide` and jdoc's `jdocmunch_guide`. Tool count 35 -> 36. Backstory: issue #296 on jcm.)
- **Version (v1.12.1):** Hygiene patch. `__version__` now derived from `importlib.metadata.version("jdatamunch-mcp")` in `__init__.py` — pyproject.toml is the single source of truth, the runtime/packaging version strings can no longer disagree. Mirrors jcm's pattern. Backstory: v1.12.0 shipped with the hardcoded literal stuck at 1.9.0, three minors stale.
- **Version (v1.12.0):** Phase-2 jData COMPLETE — `find_similar_columns`: multi-signal cross-dataset column consolidation. Fuses name (token Jaccard, snake+camel aware) + type + top-value Jaccard + cardinality + embedding cosine (when present). Union-find clustering; verdict tiers: near_duplicate / naming_drift / parallel_definition / overlapping_topic. Differs_by breakdown per pair makes verdict auditable. Mirrors jcm's find_similar_symbols.
- **GitHub:** `jgravelle/jdatamunch-mcp`
- **Python:** >=3.10
- **Index format:** INDEX_VERSION = 3 (v1→v2→v3 migrations registered in `storage/migrations.py`; v3 is additive — new runtime tables created on first ingest, legacy v2 indexes load fine)
- **Tool count:** 38 (1.6.0 added `ingest_sql_log`; 1.7.0 added `find_unused_columns`; 1.8.0 added `check_column_drop_safe`; 1.9.0 added `get_schema_impact`; 1.10.0 added `get_redaction_log` + `get_data_hotspots` v2; 1.11.0 added `data_health_radar` + `diff_data_health_radar`; 1.12.0 adds `find_similar_columns`; 1.14.0 adds `tune_weights`; 1.15.0 adds `check_embedding_drift`; 1.16.0 adds `analyze_perf`)
- **Tests:** 470 passed, 10 skipped (1.12.0)

## Key Files
```
src/jdatamunch_mcp/
  server.py                    # MCP tool definitions + call_tool dispatcher
  config.py                    # Index path, max rows env vars
  perf.py                      # (1.16.0) Per-tool latency telemetry: in-memory ring (always on) + opt-in SQLite sink (<index_path>/perf_telemetry.db, JDATAMUNCH_PERF_TELEMETRY=1). record()/latency_stats()/perf_db_query(). Backs analyze_perf; call_tool records each dispatch.
  embed_drift.py               # (1.15.0) Embedding-drift canary: 16 data-flavored strings pinned in <index_path>/embed_canary.json; capture_canary + check_drift (cosine drift vs the live provider). Reuses embeddings.detect_provider/embed_texts/cosine_similarity. Backs the check_embedding_drift tool.
  tuning.py                    # (1.14.0) Ranking-weight overrides for search_data. DEFAULT_WEIGHTS (single source of truth for the weight vector) + load_effective_weights() (defaults < global < per-dataset); persisted in <index_path>/ranking_tuning.json. The tune_weights tool's backing store.
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
    search_data.py             # Search rows by column value / pattern. Ranking weights resolved per-query via tuning.load_effective_weights (tunable by tune_weights).
    analyze_perf.py            # (1.16.0) Per-tool latency (p50/p95/max/error_rate) + result-cache hit rates; window=session (ring) or 1h/24h/7d/all (sink). Mirrors jcm/jdoc analyze_perf.
    check_embedding_drift.py   # (1.15.0) Canary-based embedding-drift detector (force=re-pin, else report cosine drift + alarm). Thin wrapper over embed_drift.py. Mirrors jcm/jdoc check_embedding_drift.
    tune_weights.py            # (1.14.0) Inspect / set / reset the search_data ranking weights. No ledger, so tuning is explicit (vs jcm/jdoc's learned). Mirrors their tune_weights agent-facing contract.
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

## Releasing
- **Tests** (`.github/workflows/test.yml`): matrix ubuntu+windows x py3.10-3.13 on push/PR to master; pytest + sdist sensitive-path check.
- **Release** (`.github/workflows/release.yml`, added v1.16.0): on every push to master, *after Tests passes*, auto-tags + creates a GitHub release when `pyproject.toml`'s version has no release yet. **Builds the wheel + sdist (`python -m build`) and attaches both to the release** — the console one-click installer reads the latest release's `.whl`, so a release MUST carry it. No-op for docs-only / non-bump pushes. Gated via `workflow_run` + `conclusion == 'success'` so a red commit never gets tagged. **Don't hand-tag on a version bump** — the workflow does it (hand-creating a release first just makes the workflow no-op, which is fine). (Asset-attaching added 2026-06-28 after v1.15.0/v1.16.0 shipped bare and broke the console installer; v1.16.0's wheel was backfilled by hand.)
- **PyPI is still manual**: `python -m build` + `twine upload dist/*` from a machine with `.pypirc`. CI has no PyPI credential. To automate, add a publish job using PyPI Trusted Publishing (OIDC, no stored secret) once the publisher is configured on pypi.org.

## Benchmarks
Real production dataset (LAPD crime records, 1M rows):

| Dataset | Rows | Cols | File Size | Avg Ratio |
|---------|-----:|-----:|----------:|----------:|
| crime.csv | 1,004,894 | 28 | 255.5 MB | **25,333x** |

Baseline = full raw CSV tokenized. jDataMunch = `describe_dataset` + `describe_column`.
Benchmark harness: `python benchmarks/harness/run_benchmark.py <file.csv>`
