# jDataMunch — V1.0.0 TODO

Actionable roadmap to a stable, production-worthy 1.x.x release.
Closure-driven: ship Phase A, declare 1.0.0. Phase B/C are post-1.0.

---

## Phase A — Required For 1.0.0 (Finish Line) — COMPLETE ✅

Shipped in 1.0.0. See CHANGELOG `[1.0.0]` for the full rollup. 266 tests passing.

### A1. Numeric stability — Welford/Kahan accumulators ✅
- [x] Replace `num_sum += num` in `_ColAcc` (profiler/column_profiler.py) with Welford online mean + Neumaier-compensated sum.
- [x] Add `num_m2` field for variance; surface `std_dev` in `ColumnProfile`.
- [x] Update `finalize_profile` to emit `mean`, `std_dev`, `variance`.
- [x] Test: extreme-magnitude (1e-9..1e9) mean accuracy verified in `tests/test_welford.py`.

### A2. T-digest quantiles + dispersion stats ✅
- [x] Vendor pure-Python t-digest under `profiler/tdigest.py`.
- [x] Replace `reservoir: list` in `_ColAcc` with t-digest.
- [x] Emit `quantiles: {p01, p25, p50, p75, p95, p99}` on numeric profiles.
- [x] Surface in `describe_column` response (via profile dict).
- [x] Test: uniform-distribution quantiles within 2% (`tests/test_tdigest.py`).

### A3. HyperLogLog cardinality fallback ✅
- [x] Add `profiler/hll.py` (m=2048, ~1.5KB/col, ~2% error).
- [x] In `_ColAcc`, maintain HLL alongside `value_counts`; once `cardinality_overflow=True`, populate `cardinality_approx`.
- [x] Add `cardinality_estimated: bool` field on `ColumnProfile`.
- [x] Surface in `describe_dataset` / `describe_column` (via profile dict).
- [x] Test: 1M-distinct value column verified within 3% (`tests/test_hll.py`).

### A4. Crash-safe ingest ✅
- [x] Write SQLite to `data.sqlite.tmp`, rename on success.
- [x] Drop `PRAGMA synchronous=OFF`; use `synchronous=NORMAL` during bulk load.
- [x] Write `_lock` file at `index_local` start, remove at end.
- [x] On any read tool, stale `_lock` + missing `index.json` → `cleanup_stale_artifacts`.
- [x] Sidecar `index.json.sha256` written atomically with `index.json`.
- [x] Test: stale-lock cleanup + no-tmp-leak post-success in `tests/test_crash_safety.py`.

### A5. `validate_index` tool ✅
- [x] New tool in `tools/validate_index.py`.
- [x] Run `PRAGMA integrity_check` on SQLite.
- [x] Compare `SELECT COUNT(*) FROM rows` vs `index.json.row_count`.
- [x] Verify schema columns match.
- [x] Verify `index.json` SHA-256 matches sidecar.
- [x] Register in `server.py` tool list.
- [x] Detect stale `_lock` and warn.

### A6. Semantic column typing ✅
- [x] New `profiler/semantic_types.py` with 13 detectors (email, url, uuid, iso_currency, phone_e164, ipv4, ipv6, iso_country, lat, lon, zip_us, boolean_text, percentage).
- [x] Each detector: `(samples, col_name) → (semantic_type | None, confidence: float)`.
- [x] Run after primitive type-rank in `finalize_profile`.
- [x] Add `semantic_type`, `semantic_confidence` to `ColumnProfile`.
- [x] Surfaced in `describe_dataset`, `describe_column` (via profile dict).
- [x] Detector tests in `tests/test_semantic_types.py`.

### A7. Type-inference confidence + violations ✅
- [x] Track per-rank counts in `_ColAcc` during full pass.
- [x] Emit `type_confidence: float` and `type_violation_samples[5]` on profile.
- [x] Surfaced via profile dict.

### A8. Profile snapshots / dataset history ✅
- [x] On every `index_local` re-index, append compact snapshot to `_history.jsonl`.
- [x] Snapshot fields: timestamp, source_hash, row_count, schema digest.
- [x] Rotated to last 50.
- [x] New tool `get_dataset_history(dataset, n=10)` registered in server.py.

### A9. Deterministic sampling ✅
- [x] Added `seed: int` parameter to `sample_rows` (method='random').
- [x] Uses seeded `random.Random(seed)` for picked rowids.
- [x] Documented in tool description.

### A10. Shared parser null-normalization ✅
- [x] New `parser/normalize.py` exporting `normalize_native(raw, source_format)`.
- [x] JSONL, Parquet, Excel parsers all route through it.
- [x] Cross-parser contract test in `tests/test_normalize.py`.

### A11. Index migration framework + INDEX_VERSION=2 ✅
- [x] `storage/migrations.py` with registry-based migration chain.
- [x] `_migrate_v1_to_v2` carries additive fields from A1–A8.
- [x] `data_store.load()` runs `migrate_to_current` automatically.
- [x] Bumped `INDEX_VERSION` to 2.
- [x] Tests in `tests/test_migrations.py`.

### A12. Correctness + safety test infrastructure ✅
- [x] **Profiling correctness**: `test_welford.py`, `test_tdigest.py`, `test_hll.py`.
- [x] **Crash-recovery test**: `test_crash_safety.py`.
- [x] **Determinism test**: `test_determinism.py` (index byte-stable + seeded sampling).
- [x] **Aggregate correctness**: `test_aggregate_correctness.py` (vs Python reference).
- [x] **Cross-parser contract test**: `test_normalize.py` CSV ↔ JSONL profile equality.
- [x] **Migration test**: `test_migrations.py` v1 → v2 idempotent.
- [x] **Validate-index test**: `test_validate_index.py` covers row-count drift, checksum drift, stale lock.
- [ ] **Large-dataset stress harness** (deferred — gated `JDM_PERF=1`, not blocking 1.0).
- [ ] **Memory-profiling harness** (deferred — captured as Phase B follow-up).

### A13. README + stability guarantees ✅
- [x] README "Stability guarantees (v1.0.0)" section: INDEX_VERSION policy, statistical correctness, crash safety, recovery flow, reproducibility.
- [x] CHANGELOG `[1.0.0]` entry covering all of Phase A.
- [x] Bumped `pyproject.toml` and `__init__.py` to 1.0.0.
- [x] Updated `CLAUDE.md` (project + global registry) to reflect 1.0.0 state.

---

## Phase B — Strongly Recommended (1.1.0 – 1.3.0) — COMPLETE ✅

Shipped in 1.1.0. See CHANGELOG `[1.1.0]`. 301 tests passing.

### B1. Read-only sandboxed `run_sql` ✅
- [x] New tool: `run_sql(sql, datasets=[a,b,...])` (`tools/run_sql.py`).
- [x] Pure-Python validation rejects non-SELECT, multi-statement, and forbidden keywords.
- [x] `PRAGMA query_only=1`, 10 s wall-clock budget via progress handler, 500-row cap.
- [x] ATTACH DATABASE per named dataset under safe schema alias.

### B2. Aggregate result cache ✅
- [x] `storage/result_cache.py`: hash key over `(tool, source_hash, normalized_args)` → JSON files in `~/.data-index/{dataset}/_cache/`.
- [x] Invalidated by `result_cache.invalidate()` on every successful re-index.
- [x] Applied to `aggregate`, `get_correlations`, `get_data_hotspots`. `_meta.cache_hit` reports.

### B3. `plan_query` agent router ✅
- [x] New tool: `plan_query(dataset, intent: str)` (`tools/plan_query.py`).
- [x] Pure routing — no LLM call.
- [x] Intents: `summarize`, `anomalies`, `compare`, `join`, `filter`, `trend`, `correlate`.

### B4. Dataset health score ✅
- [x] New tool: `get_dataset_health(dataset)` (`tools/get_dataset_health.py`).
- [x] Composite: null severity, type-confidence avg, constant-col count, PK presence, semantic typing rate, drift-free score.
- [x] Returns A–F grade + components + issue lists.

### B5. FK/PK + functional dependency discovery ✅
- [x] `suggest_keys(dataset)` ranks PK candidates with confidence + reasons.
- [x] `suggest_joins(dataset)` containment scan, ≥ 0.95 threshold, sample-based.
- [x] Cross-dataset scan capped at 20 datasets.

### B6. Streaming Parquet row-group pushdown ✅
- [x] `parquet_parser` exposes per-column logical types via `metadata['column_types']`.
- [x] `index_local` consumes pushdown types and skips the 10k-row sample inference for Parquet.
- [ ] Full row-group min/max/null_count exposure (deferred — current pushdown already eliminates one pass).

### B7. Adaptive profiling depth ✅
- [x] `depth: 'shallow' | 'standard' | 'deep'` parameter on `index_local`.
- [x] Shallow caps at 100k rows; deep precomputes correlations.
- [x] Recorded as `result.depth` in the response.

### B8. Unified `get_distribution` ✅
- [x] New tool dispatching numeric / datetime / categorical bin counts.
- [x] Numeric: equal-width bins between min/max. Datetime: month buckets. Categorical: top-n + 'other'.

### B9. BM25 in `search_data` ✅
- [x] Vendored `bm25.py` (~80 LOC, k1=1.5, b=0.75).
- [x] BM25 used in default `all` scope over column name + ai_summary + value index + samples.
- [x] Existing semantic-hybrid path preserved.

### B10. Spearman correlation ✅
- [x] `method: 'pearson' | 'spearman'` on `get_correlations`.
- [x] Spearman implemented via `ROW_NUMBER() OVER (ORDER BY col)` rank transform + Pearson on ranks.

### B11. HAVING in `aggregate` ✅
- [x] `having: list[Filter]` parameter on `aggregate`.
- [x] Substitutes aggregate expression (not alias) into HAVING — works even when alias name collides with a source column.
- [x] Operators: eq, neq, gt, gte, lt, lte, in, between, is_null.

---

## Phase C — Optional Post-V1 (1.4.0+) — COMPLETE ✅

Shipped in 1.4.0. See CHANGELOG `[1.4.0]`. 317 tests passing.

- [x] **C1.** Approximate aggregate mode — `aggregate(approximate=True)`. `count_distinct` via HLL (~2% error), `median` via t-digest, `sum`/`avg` via sampling with 95% CI half-width. Whole-dataset only.
- [x] **C2.** Dataset content fingerprint — `sha256(sorted(column_names) + first_1000_row_hash)` stored on `index.json` and surfaced in `list_datasets`. Path-independent dedup.
- [x] **C3.** Per-dataset learned null tokens — `profiler/null_learner.py` detects sentinel-looking tokens (TBD, 999, ----, UNKNOWN, …) recurring across columns. Surfaced as `index.learned_null_tokens`.
- [x] **C4.** Coarse domain classification — `summarize_dataset` appends `Likely domain: …` (geo / financial / log / event / temporal) when evidence supports it.
- [x] **C5.** Per-tool token-savings attribution — `_savings.json` tracks `per_tool[<tool>] = {tokens_saved, calls}`. Surfaced in `get_session_stats.result.per_tool`.
- [x] **C6.** Cross-session aggregate cache — already shipped via B2 `storage/result_cache.py`. Persists across sessions as JSON files; invalidated on re-index.

---

## Out of Scope (Permanently)

Do not enter the roadmap. If asked, defer to 2.x consideration:
- Predictive ML / forecasting
- Dashboards or BI UI
- Multi-tenant SaaS
- Cloud sync / hosted catalog
- Generative "AI insights" beyond the existing rule-based summarizer
- Vector DB platformization

---

## Closure Rule

Phase A defines 1.0.0. Anything not in A above does not block the 1.0 release. New requests slot into B or C. The roadmap converges.
