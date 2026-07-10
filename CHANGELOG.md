# Changelog

## [1.18.0] - 2026-07-10 - suite-parity retrieval verdict (`_meta.verdict` on search_data)

### Added

- **`search_data` now emits `_meta.verdict`** — the same agent-facing honesty
  contract the sibling code and doc MCPs ship on their search tools. An empty
  column search is positive, token-saving evidence: the index can attest "no
  column matches this" instead of leaving the agent to reformulate. Taxonomy:
  `ok` / `absent` / `degraded`.
- **`degraded`** fires when semantic search is requested but the embedding
  channel falls back to keyword-only, so absence is not proven. It takes
  precedence over `absent`.
- **`absent`** carries a `did_you_mean` list of column names containing a query
  term, so a miss redirects the agent instead of repeating the same empty query.

Honest divergence from the sibling search tools: jData scores are rank-normalized
(top hit always 1.0), so there is no calibrated confidence signal — `search_data`
emits no `low_confidence` state (it would be fabricated). Clean-room jData
implementation (new top-level `verdict.py`); only the wire shape is shared — no
cross-suite import. Additive and 1.x-compatible: `_meta.verdict` is a new key,
every existing response field is unchanged, no `INDEX_VERSION` bump, inline
compute. Tests: `tests/test_v1_18_0.py` (9).

## [1.17.0] - 2026-07-07 - MCP readOnlyHint annotations (suite parity with jcodemunch PR #361)

### Added

- **Every tool advertises `ToolAnnotations(readOnlyHint=...)`.** MCP clients that
  gate execution (Claude Code plan mode) prompted for approval on every jData
  call because tools carried no annotations. Read tools are now
  `readOnlyHint=True` (plan mode runs them silently) and the write-set is
  `False`. Applied at the `list_tools` chokepoint via a non-mutating
  `model_copy`. The write-set (`index_local`, `index_repo`, `summarize_dataset`,
  `delete_dataset`, `embed_dataset`, `ingest_sql_log`, `tune_weights`,
  `check_embedding_drift`) is any tool that can mutate persistent state under any
  argument — biased conservative, since mislabeling a writer as read-only is the
  harmful direction. Suite parity with jcodemunch-mcp (PR #361) and
  jdocmunch-mcp. Additive, 1.x-compatible (new `tools/list` field only). Tests:
  `tests/test_v1_17_0.py` (4).

## [1.16.0] - 2026-06-16 - `analyze_perf`: per-tool latency + cache-hit telemetry

Completes the sibling-parity trio (jcodemunch-mcp and jdocmunch-mcp both ship
`analyze_perf`). jData previously had no latency telemetry and an uninstrumented
result cache; this adds both, then surfaces them.

- New `perf.py`: an in-memory per-tool latency ring (always populated when
  `call_tool` fires; 500 samples/tool) plus an optional persistent SQLite sink
  at `<index_path>/perf_telemetry.db`, gated by `JDATAMUNCH_PERF_TELEMETRY=1`
  (FIFO-capped by `JDATAMUNCH_PERF_TELEMETRY_MAX_ROWS`, default 100000).
  Recording is best-effort and never breaks a tool call.
- `server.py` records each dispatch's wall-clock latency and ok/error flag.
- `storage/result_cache.py` gained per-tool hit/miss counters (`cache_stats()`);
  `get()` takes an optional `tool` so `aggregate` / `get_correlations` /
  `get_data_hotspots` attribute their cache hits.
- New `analyze_perf` tool: `window=session` reads the in-memory ring;
  `window=1h|24h|7d|all` reads the persistent sink. Returns per-tool
  p50/p95/max/error_rate, the slowest tools by p95, and result-cache hit rates
  (totals + coldest-by-tool). `tool=` narrows to one tool; `top=` caps the
  rankings.

Tool count 37 -> 38. **jData now has full agent-facing tool parity with its
siblings.** 12 new tests (`tests/test_v1_16_0.py`).

## [1.15.0] - 2026-06-16 - `check_embedding_drift`: detect silent embedding-provider drift

Column embeddings power semantic `search_data` and `find_similar_columns`. If
the embedding provider's model changes underneath a stored index (a model
revision bump, a reweight under the same name, a swapped local
sentence-transformers model), the vectors saved at index time stop matching what
the live query encoder produces and semantic ranking quietly degrades. The new
`check_embedding_drift` tool catches that, closing a sibling-parity gap
(jcodemunch-mcp and jdocmunch-mcp both ship one).

- New `embed_drift.py` pins a 16-string **canary** — deterministic strings
  spanning tabular / column semantics (identifiers, money, dates, geo,
  categories, free text), tailored to the domain jData embeds rather than code
  tokens — embedded with the active provider and stored in
  `<index_path>/embed_canary.json` (`{provider, model, dim, captured_at,
  strings, vectors}`).
- New `check_embedding_drift` tool: `force=true` re-embeds and re-pins the
  baseline; otherwise it recomputes the canary and reports `max_drift` /
  `mean_drift` / per-canary cosine, raising `alarm` when the worst canary drifts
  past `threshold` (cosine distance, default 0.05). A provider swap is reported
  even when the cosine comparison still runs.
- Reuses jData's own `embeddings.detect_provider` / `embed_texts` /
  `cosine_similarity` so the canary never drifts from the live encoder.

Tool count 36 -> 37. 9 new tests (`tests/test_v1_15_0.py`).

## [1.14.0] - 2026-06-16 - `tune_weights`: tunable search_data ranking weights

`search_data` ranks columns with a small weight vector (name / value / type
match weights plus the BM25 and semantic blend scales). Until now those weights
were hardcoded module constants and there was no way to adjust them. The new
`tune_weights` tool makes the vector tunable and persistable, closing a
sibling-parity gap (jcodemunch-mcp and jdocmunch-mcp both ship a `tune_weights`
tool over their ranker).

- New `tuning.py` holds `DEFAULT_WEIGHTS` (single source of truth for the
  vector) and `load_effective_weights()`, which resolves defaults < global
  overrides < per-dataset overrides. Overrides persist in
  `<index_path>/ranking_tuning.json` (atomic write; a corrupt file degrades to
  defaults).
- New `tune_weights` tool: omit all args to inspect the effective weights and
  their source; pass `set_weights` (a `{weight: number}` object) to override;
  pass `reset=true` to clear. Scope with `dataset`. Overrides are validated
  (unknown names / non-numeric values rejected) and clamped to each weight's
  bounds.
- `search_data` resolves the effective weights once per query and now honors a
  tuned `default_semantic_weight` when the caller omits `semantic_weight`.
  Default behavior is unchanged when no overrides exist.
- Honest divergence from the siblings: jdatamunch-mcp keeps no ranking-events
  ledger (`call_tracker` is ephemeral loop-detection), so weights are tuned
  explicitly here rather than learned from usage.

Tunable weights: `name_exact`, `name_substr`, `name_word`, `ai_summary_word`,
`value_exact`, `value_substr`, `type_boost`, `bm25_scale`, `semantic_scale`,
`default_semantic_weight`. Tool count 35 -> 36. 14 new tests
(`tests/test_v1_14_0.py`); full suite 498 passed / 10 skipped.

## [1.13.1] - 2026-06-10 - disclose the community savings meter in README

Docs-only patch. The anonymous community savings meter (random install ID +
tokens-saved counter POSTed to j.gravelle.us, default on, opt out with
`JDATAMUNCH_SHARE_SAVINGS=0`) was implemented and opt-out-able but never
described in the README. Added a "Community savings meter" disclosure to the
Token savings telemetry section, mirroring jdocmunch-mcp's README. Prompted by
PyPI's quarantine-exit guidance on the sibling package: long-term or
out-of-band operations must be disclosed in the README. No code change.

## [1.13.0] - 2026-05-14 - `tool_profile` + `disabled_tools` config (#297)

Reported by @AlexJ-StL in jcm#297: Google Antigravity caps MCP-server
tool counts at 50, and the full munch suite ships 81 + 60 + 35 = 176
tools. Sibling-parity gap with jcm. jdata now ships the same knobs
that jdoc gained in v1.64.0:

- `JDATAMUNCH_TOOL_PROFILE=core|standard|full` (default `full`).
  - `core` (10 tools): index + describe + the row-retrieval essentials.
  - `standard` (~30 tools): core + analysis tools.
  - `full` (35 tools): everything, current behavior.
- `JDATAMUNCH_DISABLED_TOOLS=tool1,tool2,...` removes named tools from
  both the listed schema and the call dispatcher.

Filtering enforced in `list_tools()` AND `call_tool()` so cached
schemas get a clear error. `jdatamunch_guide` survives tier filtering
but honors `disabled_tools` (documentation, not a control surface).

Antigravity users running all three munches can now do:

```jsonc
"jdatamunch": { "env": { "JDATAMUNCH_TOOL_PROFILE": "core" } }
"jdocmunch":  { "env": { "JDOCMUNCH_TOOL_PROFILE":  "core" } }
"jcodemunch": { "env": { "JCM_TOOL_PROFILE":        "core" } }  // or tool_profile in .jcodemunch.jsonc
```

Suite total drops to 10 + 13 + 17 = 40 tools, comfortably under 50.

## [1.12.2] - 2026-05-13 - `jdatamunch_guide` sibling-parity tool

Adds `jdatamunch_guide`, the data-MCP sibling of `jcodemunch_guide` (in
jcm since v1.84.0) and `jdocmunch_guide` (in jdoc v1.63.3). Returns the
version-current CLAUDE.md / AGENT.md policy snippet for jdatamunch-mcp
so an agent can keep a one-line CLAUDE.md (`"Call jdatamunch_guide and
strictly follow its instructions."`) instead of pasting a static block
that drifts from the installed version.

Backstory: GitHub issue #296 (Codex Desktop compatibility report on
jcodemunch-mcp) flagged the parity gap in the doc-MCP. Once jdoc shipped
its guide, jdata was the only suite member still missing one. Tool surface
is grouped into 11 categories with a quick-start path (list_datasets ->
index_local -> describe_dataset -> describe_column -> run_sql).

Tool count 35 -> 36. No tool, schema, or wire-format change for existing
tools. 465 tests pass, 1 skipped (459 baseline + 6 new in `test_v1_12_2.py`).

## [1.12.1] - 2026-05-12 - drift-proof __version__ via importlib.metadata

`src/jdatamunch_mcp/__init__.py` now derives `__version__` from
`importlib.metadata.version("jdatamunch-mcp")` instead of a hardcoded
literal. pyproject.toml is the single source of truth; the wheel's
metadata is read at import time, so the runtime version string and
the packaging version string cannot disagree by construction.

Mirrors the jcodemunch-mcp pattern (in place since v1.84.0) and
jdocmunch-mcp v1.63.2.

Backstory: v1.12.0 shipped with `__version__` hardcoded at 1.9.0,
three minors stale. Nothing failed because the runtime version string
is rarely consulted; the bug would have surfaced as wrong telemetry
labels or wrong baseline filenames in future work that branched on it.

Source-checkout callers without `pip install` see
`__version__ = "unknown"`.

No tool, schema, or wire-format changes.

## [1.12.0] — 2026-05-12 — `find_similar_columns` (Phase-2 jData COMPLETE)

Multi-signal cross-dataset column consolidation tool. Mirrors jcm's
`find_similar_symbols` and jdoc's `find_similar_sections` — fuses
several similarity signals into a composite score, clusters via
union-find, classifies each cluster into a verdict tier.

### Signal fusion

| Signal      | Source                                                    |
|-------------|-----------------------------------------------------------|
| name        | Token-overlap Jaccard (snake + camel split + lowercase)   |
| type        | 1.0 same type, 0.5 same numeric family, 0.0 otherwise     |
| value       | Jaccard on top_values when both columns are low-cardinality |
| cardinality | 1 - abs(ratio_a - ratio_b) where ratio = card/row_count   |
| embedding   | Cosine on column embeddings (when present on both sides)  |

Weighting:

- with embeddings:    `emb 0.50 + name 0.20 + value 0.15 + type 0.10 + card 0.05`
- without embeddings: `name 0.45 + value 0.30 + type 0.15 + card 0.10`

### Verdict tiers

- `near_duplicate`      — composite ≥ 0.85 and types match
- `naming_drift`        — composite ≥ 0.70 and name_sim < 0.5
- `parallel_definition` — composite ≥ 0.70 and name_sim ≥ 0.7
- `overlapping_topic`   — composite ≥ 0.50

### Use cases

- Find duplicate columns to consolidate before a migration.
- Surface naming drift across teams (`email` vs `email_address`).
- Detect the same conceptual column spread across multiple datasets
  (`users.email` and `customers.email`) that probably wants one source
  of truth.

`differs_by` breakdown per pair calls out which signals fired weakly so
the verdict is auditable. Returns full clusters with members + pairs +
per-cluster strongest verdict.

### Stats

- Tool count: 35 (`find_similar_columns` new)
- Tests: 470 passed, 10 skipped (+15 new — 7 pure-function + 8 integration)

This completes Phase 2 for jData. Remaining Phase-2 work: jDoc's
`doc_health_radar` + `diff_doc_health_radar` and `get_doc_pr_risk_profile`.

---

## [1.11.0] — 2026-05-12 — `data_health_radar` + `diff_data_health_radar`

Six-axis health radar for tabular datasets, plus a pure-function diff
helper for snapshot-to-snapshot comparisons. Mirrors jcm's
`health_radar.py` shape (six-axis + optional seventh runtime axis +
A-F grade + axis-by-axis diff).

### New: `data_health_radar` MCP tool

Composes per-column signals from index.json + history snapshots into a
0-100 score across six axes plus a composite + letter grade:

| Axis              | Source                                          |
|-------------------|-------------------------------------------------|
| null_health       | 100 − mean(null_pct) across columns             |
| type_confidence   | mean(type_confidence) × 100                     |
| cardinality_health| linear penalty per constant column              |
| pk_presence       | has PK candidate → 100, else 50                 |
| semantic_coverage | semantic_type detected / typeable candidates    |
| schema_stability  | drift-free between first/last history snapshot  |
| runtime_coverage  | (optional) % of columns with traffic in window  |

`schema_stability` is omitted when fewer than two history snapshots
exist. `runtime_coverage` is omitted when no runtime traces are
ingested or `include_runtime=False`. Omitted axes appear in the
`omitted_axes` list, never silently — they don't count toward the
composite so radars stay comparable across datasets with different
ingest states.

### New: `diff_data_health_radar` MCP tool

Pure function: takes two radar payloads and returns per-axis deltas,
composite delta, grade change, lists of regressions and improvements
(threshold: 3 points), and a one-line verdict. No I/O — pass radar
payloads from disk, CI artifacts, or two consecutive
`data_health_radar` calls.

### Stats

- Tool count: 34 (+ `data_health_radar`, `diff_data_health_radar`)
- Tests: 455 passed, 10 skipped (+13 new across the two tools)

---

## [1.10.0] — 2026-05-12 — `get_redaction_log` + `get_data_hotspots` v2 (Phase-2 opener)

First Phase-2 release. Two thin tools bundled because both are reads off
already-populated tables with no behavior to bake individually.

### New: `get_redaction_log` MCP tool

Forensic accounting of PII redactions per dataset. Reads
`runtime_redaction_log` (populated by `ingest_sql_log` with
`redact=True`, the default) and surfaces per-pattern counts so operators
can verify the redaction chokepoint is actually firing on production
traffic.

- Filters by `source` (today: `sql_log`) and `since_days` window.
- Returns `{dataset, sources, since_iso, patterns[], total_redactions}`.
- Empty patterns list is **not** an error — it's a valid "nothing
  scrubbed yet" state, distinguished from invalid-source / unknown-
  dataset refusals which return structured `reason` codes.
- Mirrors jcodemunch-mcp's `get_redaction_log` (Phase 6) but keyed on
  `dataset_id` and reads jData's `(pattern, count, source, last_seen)`
  table shape.

### Enhanced: `get_data_hotspots` v2 (runtime traffic fusion)

Adds a 4th signal — **runtime traffic** — when traces have been
ingested. Score becomes
`null(0.30) + cardinality(0.20) + outlier(0.20) + traffic(0.30)`. The
traffic axis is normalised by the most-called column in the dataset,
amplifying risk on heavily-queried problematic columns. A 100%-null
column nobody queries is now correctly less urgent than a 30%-null
column queried 10k times a day.

When `include_runtime=True` (default) but no traces are ingested, the
response carries an **honest-hint caveat** in `_meta.runtime_caveat`
rather than silently falling back to v1 scoring without disclosure.
`runtime_data_present` is surfaced on every response. v1 scoring is
preserved bit-for-bit when traces are absent or `include_runtime=False`.
Honest-hint pattern lifted from `check_column_drop_safe` v1.8.0 and
jcm's `check_delete_safe` v1.108.6.

### Stats

- Tool count: 32 (1.10.0 adds `get_redaction_log`)
- Tests: 442 passed, 10 skipped (+ 8 new across the two tools)

---

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
