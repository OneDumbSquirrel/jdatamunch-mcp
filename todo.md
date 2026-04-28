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

## Phase B — Strongly Recommended (1.1.0 – 1.3.0)

### B1. Read-only sandboxed `run_sql`
- [ ] New tool: `run_sql(sql, datasets=[a,b,...])`.
- [ ] AST validation rejecting non-SELECT (parse with `sqlglot` or pure-Python statement check).
- [ ] `PRAGMA query_only=1`, statement timeout, hard row cap.
- [ ] ATTACH DATABASE per named dataset.

### B2. Aggregate result cache
- [ ] Hash `(dataset, source_hash, aggregations, group_by, filters, order_by, limit)` → cached JSON in `~/.data-index/{dataset}/_cache/`.
- [ ] Invalidate when `source_hash` changes.
- [ ] Apply to `aggregate`, `get_correlations`, `get_data_hotspots`.

### B3. `plan_query` agent router
- [ ] New tool: `plan_query(dataset, intent: str)` returning ranked tool sequence.
- [ ] Pure routing logic over schema metadata; no LLM call.
- [ ] Built-in intents: `summarize`, `find anomalies`, `compare to`, `join with`, `filter`.

### B4. Dataset health score
- [ ] New tool: `get_dataset_health(dataset)`.
- [ ] Composite: null severity, type-confidence avg, constant-col count, near-duplicate rows, schema-drift flags, PK presence.
- [ ] Returns A–F grade + breakdown.

### B5. FK/PK + functional dependency discovery
- [ ] `suggest_keys(dataset)`: scan unique columns, surface PK candidates with confidence.
- [ ] `suggest_joins(dataset)`: cross-dataset containment scan; threshold `containment >= 0.95` against PK candidates of other datasets.
- [ ] Limit cross-dataset scan to 20 datasets, sample-based.

### B6. Streaming Parquet row-group pushdown
- [ ] Detect Parquet source in `index_local`; branch to `parquet_parser.profile_metadata()`.
- [ ] Read row-group min/max/null_count from Parquet metadata; sample rows only for value distribution.
- [ ] Skip full row materialization unless deep mode requested.

### B7. Adaptive profiling depth
- [ ] Add `depth: 'shallow' | 'standard' | 'deep'` to `index_local`.
- [ ] Shallow: first 100k rows + structural schema only.
- [ ] Standard: current behavior.
- [ ] Deep: + correlations precomputed + embeddings.
- [ ] Record `profile_depth` in `index.json`.

### B8. Unified `get_distribution`
- [ ] New tool: `get_distribution(dataset, column, bins=20, by=optional_groupby)`.
- [ ] Numeric → equal-width bins; datetime → time-bucket bins; categorical ordinal → top-n + other.

### B9. BM25 in `search_data`
- [ ] Replace substring scoring with BM25 over column name + ai_summary + sample values.
- [ ] Vendor ~50 LOC BM25; preserve existing semantic-hybrid path.

### B10. Spearman correlation
- [ ] Add `method: 'pearson' | 'spearman'` to `get_correlations`.
- [ ] Spearman: rank-transform via SQLite `ROW_NUMBER() OVER (ORDER BY col)` then Pearson on ranks.

### B11. HAVING in `aggregate`
- [ ] Add `having: list[Filter]` parameter; filters reference aggregation aliases.
- [ ] Validate aliases exist; emit as SQL `HAVING` clause.

---

## Phase C — Optional Post-V1 (1.4.0+)

- [ ] **C1.** Approximate aggregate mode (`approximate=True`) with confidence intervals — `count_distinct` via HLL, `median` via t-digest, `sum/avg` sampled.
- [ ] **C2.** Dataset fingerprint dedup: `sha256(sorted(column_names) + first_1000_row_hash)` exposed in `list_datasets`.
- [ ] **C3.** Per-dataset learned null tokens (frequency-based detection of dataset-specific null markers).
- [ ] **C4.** Coarse domain classification in `summarize_dataset` (financial / temporal / geo / log / event).
- [ ] **C5.** Per-tool token-savings attribution in `_savings.json`.
- [ ] **C6.** Cross-session aggregate snapshot cache.

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
