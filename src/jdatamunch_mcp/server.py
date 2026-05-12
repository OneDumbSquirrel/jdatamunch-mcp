"""MCP server for jdatamunch-mcp."""

import argparse
import asyncio
import json
import os
import sys
import traceback
from typing import Optional

from mcp.server import Server
from mcp.types import Tool, TextContent, Resource

from .tools.index_local import index_local
from .tools.list_datasets import list_datasets
from .tools.describe_dataset import describe_dataset
from .tools.describe_column import describe_column
from .tools.search_data import search_data
from .tools.get_rows import get_rows
from .tools.aggregate import aggregate
from .tools.sample_rows import sample_rows
from .tools.get_session_stats import get_session_stats
from .tools.get_schema_drift import get_schema_drift
from .tools.get_data_hotspots import get_data_hotspots
from .tools.summarize_dataset import summarize_dataset as summarize_dataset_tool
from .tools.index_repo import index_repo
from .tools.get_correlations import get_correlations
from .tools.join_datasets import join_datasets
from .tools.delete_dataset import delete_dataset
from .tools.embed_dataset import embed_dataset
from .tools.list_repos import list_repos
from .tools.validate_index import validate_index
from .runtime import ingest_sql_log_file
from .tools.find_unused_columns import find_unused_columns
from .tools.check_column_drop_safe import check_column_drop_safe
from .tools.get_schema_impact import get_schema_impact
from .tools.get_redaction_log import get_redaction_log
from .tools.data_health_radar import data_health_radar
from .tools.health_radar import diff_data_health_radar
from .tools.get_dataset_history import get_dataset_history
from .tools.get_dataset_health import get_dataset_health
from .tools.suggest_keys import suggest_keys
from .tools.suggest_joins import suggest_joins
from .tools.get_distribution import get_distribution
from .tools.plan_query import plan_query
from .tools.run_sql import run_sql
from .budget import enforce_budget
from .call_tracker import record_call

server = Server("jdatamunch-mcp")


@server.list_tools()
async def list_tools() -> list[Tool]:
    """List all available tools."""
    return [
        Tool(
            name="index_local",
            description=(
                "Index a local data file (CSV, Excel, Parquet, or JSONL). Profiles all columns, "
                "detects types, computes statistics, and loads rows into SQLite for fast filtered "
                "retrieval. Set incremental=true (default) to skip re-indexing if file is unchanged."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute path to data file (.csv, .tsv, .xlsx, .xls, .parquet, .jsonl, .ndjson)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Dataset identifier override (defaults to filename stem)",
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "Skip re-index if file hash unchanged (default true)",
                        "default": True,
                    },
                    "encoding": {
                        "type": "string",
                        "description": "File encoding override (auto-detected if omitted)",
                    },
                    "delimiter": {
                        "type": "string",
                        "description": "CSV delimiter override (auto-detected if omitted)",
                    },
                    "header_row": {
                        "type": "integer",
                        "description": "Row number containing column headers, 0-indexed (default 0)",
                        "default": 0,
                    },
                    "sheet": {
                        "type": "string",
                        "description": "Excel sheet name to index (default: first sheet)",
                    },
                    "depth": {
                        "type": "string",
                        "enum": ["shallow", "standard", "deep"],
                        "description": "Profiling depth (B7). 'shallow' caps at 100k rows for fast first-look; 'standard' is the full profile (default); 'deep' additionally precomputes correlations.",
                        "default": "standard",
                    },
                },
                "required": ["path"],
            },
        ),
        Tool(
            name="index_repo",
            description=(
                "Index data files from a GitHub repository. Discovers CSV, Excel, Parquet, "
                "and JSONL files, downloads them, and indexes each via the same pipeline as "
                "index_local. Datasets are named {owner}--{repo}--{filename}. "
                "Max 50 MB per file, 20 files per repo. Set GITHUB_TOKEN env var for "
                "private repos or to avoid rate limits."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "GitHub repo URL or owner/repo string (e.g. 'pandas-dev/pandas' or 'https://github.com/pandas-dev/pandas')",
                    },
                    "incremental": {
                        "type": "boolean",
                        "description": "Skip re-index if HEAD SHA unchanged (default true)",
                        "default": True,
                    },
                    "github_token": {
                        "type": "string",
                        "description": "GitHub token override (defaults to GITHUB_TOKEN env var)",
                    },
                },
                "required": ["url"],
            },
        ),
        Tool(
            name="list_datasets",
            description="List all indexed datasets with summary statistics.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_repos",
            description=(
                "List GitHub repositories indexed via index_repo. Shows repo name, "
                "HEAD SHA, dataset count, total rows, and dataset names for each repo."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="describe_dataset",
            description=(
                "Primary orientation tool. Returns every column's name, type, cardinality, "
                "null%, and sample values. A single call replaces reading the entire source file. "
                "Equivalent to opening a spreadsheet and reading the column headers + stats. "
                "On wide tables (60+ columns), results are auto-paginated — use columns=[] to "
                "select specific ones, or columns_offset to page through remaining columns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets or index_local)",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Filter to specific columns (default: all)",
                    },
                    "columns_offset": {
                        "type": "integer",
                        "description": "Pagination offset for wide tables (default 0)",
                        "default": 0,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="describe_column",
            description=(
                "Deep profile of a single column. Full value distribution for low-cardinality "
                "columns, histogram bins for numeric, temporal range for datetime. "
                "top_n capped at 200; histogram_bins capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "column": {
                        "type": "string",
                        "description": "Column name or column ID (e.g. 'lapd-crime::AREA NAME#column')",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Top values to return for categorical columns (default 20)",
                        "default": 20,
                    },
                    "histogram_bins": {
                        "type": "integer",
                        "description": "Bins for numeric histograms (default 10)",
                        "default": 10,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from value_distribution, top_values, and sample_values (default true). Numeric stats and counts are never altered. Set false for raw values when working with data you own.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to redact on top of the built-in set.",
                    },
                },
                "required": ["dataset", "column"],
            },
        ),
        Tool(
            name="search_data",
            description=(
                "Search across column names and values. Returns column-level results with IDs "
                "— tells you where to look, not the data itself. Use before get_rows or describe_column. "
                "max_results capped at 50. Set semantic=true for embedding-based search (requires "
                "an embedding provider: JDATAMUNCH_EMBED_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "query": {
                        "type": "string",
                        "description": "Natural-language or keyword query",
                    },
                    "search_scope": {
                        "type": "string",
                        "enum": ["all", "schema", "values"],
                        "description": "Limit search to schema only, values only, or all (default 'all')",
                        "default": "all",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum results to return (default 10)",
                        "default": 10,
                    },
                    "semantic": {
                        "type": "boolean",
                        "description": "Enable semantic search via embeddings (default false). Requires embedding provider.",
                        "default": False,
                    },
                    "semantic_weight": {
                        "type": "number",
                        "description": "Weight for semantic score in hybrid ranking. 0.0 = pure keyword, 1.0 = pure semantic (default 0.5).",
                        "default": 0.5,
                    },
                    "semantic_only": {
                        "type": "boolean",
                        "description": "Skip keyword scoring entirely; use only embeddings (default false).",
                        "default": False,
                    },
                },
                "required": ["dataset", "query"],
            },
        ),
        Tool(
            name="get_rows",
            description=(
                "Filtered row retrieval via structured filters. All filters are SQL-parameterized "
                "(no injection). Operators: eq, neq, gt, gte, lt, lte, contains, in, is_null, between. "
                "Use columns=[] to project — reduces tokens significantly on wide tables. "
                "Prefer aggregate() for summaries over paginating through rows."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "filters": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "op": {
                                    "type": "string",
                                    "enum": ["eq", "neq", "gt", "gte", "lt", "lte",
                                             "contains", "in", "is_null", "between"],
                                },
                                "value": {},
                            },
                            "required": ["column", "op"],
                        },
                        "description": "Filter conditions (ANDed). E.g. [{\"column\": \"AREA NAME\", \"op\": \"eq\", \"value\": \"Hollywood\"}]",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column projection — reduces tokens (default: all)",
                    },
                    "order_by": {"type": "string", "description": "Column to sort by"},
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort direction (default 'asc')",
                        "default": "asc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows returned (default 50, hard cap 500)",
                        "default": 50,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0)",
                        "default": 0,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials (emails, SSNs, Luhn-valid credit cards, JWTs, API keys, PEM blocks, AWS keys, GitHub/Slack tokens) from row cells before return (default true). Numeric cells are never altered. _meta.redaction reports cells_redacted + per-pattern counts.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set. Invalid patterns are silently skipped (reported in _meta.redaction.invalid_custom_patterns).",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to exempt from redaction (e.g. an `email_hashed` column where the email pattern would false-positive).",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="aggregate",
            description=(
                "Server-side aggregations (GROUP BY). Saves orders of magnitude in tokens "
                "vs returning rows for the LLM to aggregate. Functions: count, sum, avg, "
                "min, max, count_distinct, median. limit capped at 1000."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "aggregations": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "column": {"type": "string"},
                                "function": {
                                    "type": "string",
                                    "enum": ["count", "sum", "avg", "min", "max",
                                             "count_distinct", "median"],
                                },
                                "alias": {"type": "string"},
                            },
                            "required": ["column", "function"],
                        },
                        "description": "Aggregation specs. Use column='*' for COUNT(*).",
                    },
                    "group_by": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Group-by columns. Empty = whole-dataset aggregate.",
                    },
                    "filters": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter rows before aggregating (same syntax as get_rows)",
                    },
                    "having": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Post-aggregation filter on aggregation aliases (B11). Each item: {\"column\": <alias>, \"op\": eq|neq|gt|gte|lt|lte|in|between|is_null, \"value\": ...}",
                    },
                    "order_by": {"type": "string", "description": "Column or alias to sort by"},
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "default": "desc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max groups returned (default 50)",
                        "default": 50,
                    },
                    "approximate": {
                        "type": "boolean",
                        "description": "Approximate-mode aggregation (C1). Routes count_distinct → HyperLogLog (~2% error), median → t-digest (~1% error), sum/avg → sampled estimator with 95% confidence interval. Whole-dataset only.",
                        "default": False,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from group-by column values (default true). Aggregate values (counts, sums, etc.) are never altered.",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Group-by column names to exempt from redaction.",
                    },
                },
                "required": ["dataset", "aggregations"],
            },
        ),
        Tool(
            name="sample_rows",
            description=(
                "Return a sample of rows. Useful for understanding data shape "
                "without prior knowledge. Method: 'head', 'tail', or 'random'. "
                "Use columns=[] on wide tables to reduce response size. "
                "Pass seed (int) with method='random' for deterministic, "
                "reproducible sampling."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "n": {
                        "type": "integer",
                        "description": "Rows to sample (default 5, max 100)",
                        "default": 5,
                    },
                    "method": {
                        "type": "string",
                        "enum": ["head", "tail", "random"],
                        "description": "Sampling method (default 'head')",
                        "default": "head",
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column projection (default: all)",
                    },
                    "seed": {
                        "type": "integer",
                        "description": "Deterministic seed for method='random' (omitted = non-deterministic)",
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from sampled cells before return (default true).",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Column names to exempt from redaction.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_schema_drift",
            description=(
                "Compare schema (columns, types, nullability) between two indexed datasets. "
                "Detects added/removed columns, type changes, and null-rate shifts. "
                "Pure in-memory comparison — no re-reading source files. "
                "Useful for detecting schema changes between dataset versions. "
                "Assessment: 'identical' | 'additive' (only additions) | 'breaking' (removals or type changes)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_a": {
                        "type": "string",
                        "description": "First dataset identifier (baseline)",
                    },
                    "dataset_b": {
                        "type": "string",
                        "description": "Second dataset identifier (comparison target)",
                    },
                },
                "required": ["dataset_a", "dataset_b"],
            },
        ),
        Tool(
            name="get_data_hotspots",
            description=(
                "Return the highest-risk columns in a dataset ranked by a composite score "
                "combining: null rate, cardinality anomalies, numeric outlier spread, and "
                "(v1.10.0) runtime traffic from runtime_query_calls when traces exist. "
                "When include_runtime is true but no traces are ingested, the response "
                "carries an honest-hint caveat in _meta.runtime_caveat rather than silently "
                "scoring on static signals alone. top_n capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Number of hotspot columns to return (default 10, max 50)",
                        "default": 10,
                    },
                    "include_runtime": {
                        "type": "boolean",
                        "default": True,
                        "description": "Fuse traffic signal from runtime_query_calls when available.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window for the traffic signal. Default 30.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_correlations",
            description=(
                "Compute pairwise Pearson correlations between numeric columns. "
                "Returns pairs sorted by |r| descending, filtered to significant correlations. "
                "Use this to discover relationships in the data without manual exploration. "
                "top_n capped at 200."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier",
                    },
                    "min_abs_correlation": {
                        "type": "number",
                        "description": "Minimum |r| to include in results (default 0.3)",
                        "default": 0.3,
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Restrict to specific numeric columns (default: all numeric)",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "Max pairs to return (default 20, max 200)",
                        "default": 20,
                    },
                    "method": {
                        "type": "string",
                        "enum": ["pearson", "spearman"],
                        "description": "Correlation method (default 'pearson'). Spearman is rank-based — robust to outliers and monotonic non-linear relationships (B10).",
                        "default": "pearson",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="join_datasets",
            description=(
                "Join two indexed datasets via SQL JOIN. Uses ATTACH DATABASE to combine "
                "two SQLite stores into one query. Supports inner, left, right, and cross joins. "
                "Use columns_a/columns_b to project — reduces tokens on wide tables. "
                "Row limit capped at 500. Prefer aggregate() on join results for summaries."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_a": {
                        "type": "string",
                        "description": "First dataset identifier (left side of join)",
                    },
                    "dataset_b": {
                        "type": "string",
                        "description": "Second dataset identifier (right side of join)",
                    },
                    "join_column_a": {
                        "type": "string",
                        "description": "Column from dataset_a to join on",
                    },
                    "join_column_b": {
                        "type": "string",
                        "description": "Column from dataset_b to join on",
                    },
                    "join_type": {
                        "type": "string",
                        "enum": ["inner", "left", "right", "cross"],
                        "description": "Join type (default 'inner')",
                        "default": "inner",
                    },
                    "columns_a": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to select from dataset_a (default: first 30)",
                    },
                    "columns_b": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Columns to select from dataset_b (default: first 30)",
                    },
                    "filters_a": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter dataset_a rows (same syntax as get_rows filters)",
                    },
                    "filters_b": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": "Pre-filter dataset_b rows (same syntax as get_rows filters)",
                    },
                    "order_by": {
                        "type": "string",
                        "description": "Column to sort results by",
                    },
                    "order_dir": {
                        "type": "string",
                        "enum": ["asc", "desc"],
                        "description": "Sort direction (default 'asc')",
                        "default": "asc",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max rows returned (default 50, hard cap 500)",
                        "default": 50,
                    },
                    "offset": {
                        "type": "integer",
                        "description": "Pagination offset (default 0)",
                        "default": 0,
                    },
                },
                "required": ["dataset_a", "dataset_b", "join_column_a", "join_column_b"],
            },
        ),
        Tool(
            name="summarize_dataset",
            description=(
                "Generate natural-language summaries for a dataset and all its columns. "
                "Works on already-indexed datasets — reads profiles from index.json, "
                "generates summaries, and writes them back. No re-parsing of source files. "
                "Summaries are also auto-generated during index_local."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets)",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="delete_dataset",
            description=(
                "Delete an indexed dataset and its SQLite store. Frees disk space. "
                "Irreversible — the dataset must be re-indexed to use again."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier to delete (from list_datasets)",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="embed_dataset",
            description=(
                "Precompute column embeddings for semantic search. Optional warm-up — "
                "search_data with semantic=true lazily embeds on first use. Running "
                "embed_dataset upfront eliminates that latency. Requires an embedding "
                "provider (JDATAMUNCH_EMBED_MODEL, GOOGLE_API_KEY, or OPENAI_API_KEY)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {
                        "type": "string",
                        "description": "Dataset identifier (from list_datasets)",
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Recompute all embeddings even if cached (default false)",
                        "default": False,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_session_stats",
            description="Return cumulative token savings and cost avoided across all tool calls.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="validate_index",
            description=(
                "Verify an indexed dataset's on-disk integrity. Runs SQLite "
                "PRAGMA integrity_check, cross-checks row count and column "
                "list against index.json, and verifies index.json content "
                "hash. Reports stale-lock state from interrupted index_local "
                "runs. Returns overall_status: 'ok' | 'warning' | 'error'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_dataset_history",
            description=(
                "Return the last N profile snapshots for a dataset. "
                "Snapshots are appended on every successful index_local — "
                "use this to detect schema/content drift over multiple "
                "ingests of the same dataset. n capped at 50."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "n": {
                        "type": "integer",
                        "description": "Number of snapshots to return (default 10, max 50)",
                        "default": 10,
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_dataset_health",
            description=(
                "Composite quality grade (A–F) for a dataset (B4). Combines "
                "null severity, type-confidence, constant-column count, "
                "primary-key presence, semantic-typing coverage, and drift "
                "history into a single score with a structured breakdown."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="suggest_keys",
            description=(
                "Rank primary-key candidates for a dataset (B5). Each entry "
                "carries a confidence score plus the reasons that raised it "
                "(integer column, UUID format, no nulls, exact-count unique)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="suggest_joins",
            description=(
                "Discover FK candidates between this dataset and other "
                "indexed datasets (B5). For each non-PK column in the source, "
                "scans up to 20 other datasets' PK candidates and proposes "
                "joins where containment ≥ 95%. Sample-based (500 distinct "
                "values per source column)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Source dataset identifier"},
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="get_distribution",
            description=(
                "Unified bin-counts for any column type (B8). Numeric → "
                "equal-width bins between min/max; datetime → time-bucket "
                "bins; categorical / string → top-n + 'other' bucket. "
                "Token-cheap way to ask 'what does this column look like?'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "column": {"type": "string", "description": "Column name"},
                    "bins": {
                        "type": "integer",
                        "description": "Number of bins / categories to return (default 20, max 100)",
                        "default": 20,
                    },
                },
                "required": ["dataset", "column"],
            },
        ),
        Tool(
            name="plan_query",
            description=(
                "Map a natural-language intent into a ranked tool-call "
                "sequence for the given dataset (B3). Pure routing — no LLM "
                "call. Built-in intents: summarize, anomalies, compare, "
                "join, filter, trend, correlate."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string", "description": "Dataset identifier"},
                    "intent": {
                        "type": "string",
                        "description": "Natural-language intent (e.g. 'summarize', 'find anomalies', 'join with X', 'trend over time'). Default 'summarize'.",
                        "default": "summarize",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="run_sql",
            description=(
                "Read-only sandboxed SQL escape hatch (B1). Accepts a single "
                "SELECT (or WITH … SELECT) statement. The first dataset is "
                "the main connection; additional datasets are ATTACHed under "
                "schema names (e.g. `<dataset>.rows`). Statement runs under "
                "PRAGMA query_only=1 with a 10-second budget and 500-row cap. "
                "Use this for HAVING / window functions / CTEs / multi-way "
                "joins that the structured tools don't cover."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "sql": {"type": "string", "description": "SELECT or WITH … SELECT statement"},
                    "datasets": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Indexed datasets to attach. Order matters: datasets[0] is the main connection.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Row cap (default 500, hard max 500)",
                        "default": 500,
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / credentials from result cells before return (default true).",
                        "default": True,
                    },
                    "redact_patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Additional Python regex patterns to layer on top of the built-in set.",
                    },
                    "redact_skip_columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Result column names to exempt from redaction.",
                    },
                },
                "required": ["sql", "datasets"],
            },
        ),
        Tool(
            name="get_schema_impact",
            description=(
                "Transitive impact of a column-level schema change (drop_column, "
                "rename_column, retype_column). Walks the inferred FK graph to "
                "max_depth, surfaces direct + transitive hits across datasets, "
                "and normalises blast_score to [0, 1]. For retype_column, also "
                "flags type_mismatch entries at FK edges whose partner type "
                "wouldn't survive the retype. Read-only."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "column": {"type": "string", "description": "Column name (case-insensitive)."},
                    "kind": {
                        "type": "string",
                        "enum": ["drop_column", "rename_column", "retype_column"],
                        "default": "drop_column",
                    },
                    "new_name": {
                        "type": "string",
                        "description": "Required for rename_column.",
                    },
                    "new_type": {
                        "type": "string",
                        "description": "Required for retype_column. e.g. integer / string / float.",
                    },
                    "max_depth": {
                        "type": "integer",
                        "default": 3,
                        "description": "BFS depth over the inferred FK graph.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Runtime traffic look-back.",
                    },
                },
                "required": ["dataset_id", "column"],
            },
        ),
        Tool(
            name="check_column_drop_safe",
            description=(
                "Composite preflight: is this column safe to drop? Fuses four "
                "signals — primary-key status, foreign-key participation, "
                "cross-dataset name match, and runtime traffic — into a single "
                "verdict plus ranked blockers and a recommended_action. "
                "Verdict tiers: pk_blocking, fk_blocking, runtime_observed, "
                "cross_dataset_blocking, safe_to_drop. Read-only. The killer "
                "feature of the Phase-1 sibling-parity batch."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "column": {"type": "string", "description": "Column name (case-insensitive)."},
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Look-back window for runtime traffic. Default 30.",
                    },
                },
                "required": ["dataset_id", "column"],
            },
        ),
        Tool(
            name="find_unused_columns",
            description=(
                "Surface columns with zero or stale runtime traffic. Reads "
                "runtime_query_calls (populated by ingest_sql_log) and surfaces "
                "columns that haven't been queried within `window_days`. "
                "Excludes primary-key candidates and audit fields (created_at / "
                "updated_at / dbt_*) by default. Refuses to run with explicit "
                "error when no runtime data has been ingested — would otherwise "
                "trivially flag every column."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Look-back window. Default 30.",
                    },
                    "min_calls": {
                        "type": "integer",
                        "default": 0,
                        "description": "Floor for 'considered used' within window. Default 0.",
                    },
                    "exclude_pk": {
                        "type": "boolean",
                        "default": True,
                        "description": "Skip primary-key candidates. Default true.",
                    },
                    "exclude_audit": {
                        "type": "boolean",
                        "default": True,
                        "description": "Skip audit columns (created_at, updated_at, dbt_*, etc). Default true.",
                    },
                },
                "required": ["dataset_id"],
            },
        ),
        Tool(
            name="ingest_sql_log",
            description=(
                "Ingest a SQL log file (pg_stat_statements CSV or generic JSONL, "
                ".gz transparently) into the per-dataset runtime tables. Each "
                "query is parsed for table + column refs, redacted at the "
                "chokepoint (string + numeric literals + cell-PII registry), "
                "and rolled up into runtime_query_calls keyed by "
                "(fingerprint, table, column). Tables in the log that don't "
                "match any indexed dataset count as unmapped. Foundational "
                "primitive for find_unused_columns, check_column_drop_safe, "
                "and data_health_radar (v1.6.0 sibling-parity Phase 1)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Path to a CSV / JSONL / .gz log file.",
                    },
                    "source": {
                        "type": "string",
                        "description": "pg_stat_statements | jsonl | auto (default — sniff by extension).",
                        "default": "auto",
                    },
                    "redact": {
                        "type": "boolean",
                        "description": "Scrub PII / literals before persisting. Default true.",
                        "default": True,
                    },
                    "max_rows": {
                        "type": "integer",
                        "description": "Hard cap on ingested rows. Default 100000.",
                        "default": 100000,
                    },
                },
                "required": ["file_path"],
            },
        ),
        Tool(
            name="data_health_radar",
            description=(
                "Six-axis health radar for a dataset: null_health, type_confidence, "
                "cardinality_health, pk_presence, semantic_coverage, schema_stability "
                "(omitted when <2 history snapshots). Optional 7th axis runtime_coverage "
                "when traces ingested. Returns 0-100 score per axis + composite + A-F "
                "grade. Pairs with diff_data_health_radar for snapshot deltas. "
                "Mirrors jcm's six-axis health radar."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset": {"type": "string"},
                    "include_runtime": {
                        "type": "boolean",
                        "default": True,
                        "description": "Fuse runtime_coverage axis when traces exist.",
                    },
                    "window_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback for the runtime axis. Default 30.",
                    },
                },
                "required": ["dataset"],
            },
        ),
        Tool(
            name="diff_data_health_radar",
            description=(
                "Diff two data_health_radar payloads. Pure function — pass the `radar` "
                "sub-field from two data_health_radar responses (e.g. yesterday vs today). "
                "Returns per-axis deltas, composite delta, grade change, regression and "
                "improvement lists (threshold: 3 points), one-line verdict."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "baseline": {
                        "type": "object",
                        "description": "Baseline radar payload (e.g. yesterday's snapshot).",
                    },
                    "current": {
                        "type": "object",
                        "description": "Current radar payload (e.g. today's snapshot).",
                    },
                },
                "required": ["baseline", "current"],
            },
        ),
        Tool(
            name="get_redaction_log",
            description=(
                "Forensic accounting of PII redactions for a dataset. Returns "
                "per-pattern counts from runtime_redaction_log (populated by "
                "ingest_sql_log with redact=True), so operators can verify the "
                "chokepoint is firing on production traffic. Filter by source "
                "and lookback window. Empty result with no traces ingested is "
                "not an error — it just means no scrubbing has happened yet."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataset_id": {"type": "string"},
                    "source": {
                        "type": "string",
                        "description": "Optional source filter. Today: 'sql_log'.",
                    },
                    "since_days": {
                        "type": "integer",
                        "default": 30,
                        "description": "Lookback window for last_seen. Default 30.",
                    },
                },
                "required": ["dataset_id"],
            },
        ),
    ]


@server.list_resources()
async def list_resources() -> list[Resource]:
    """Return empty resource list for client compatibility."""
    return []


@server.list_prompts()
async def list_prompts() -> list:
    """Return empty prompt list for client compatibility."""
    return []


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Dispatch tool calls to implementations."""
    storage_path = os.environ.get("DATA_INDEX_PATH")

    try:
        # Anti-loop detection for row-retrieval tools
        dataset_arg = arguments.get("dataset", "")
        if name in ("get_rows", "sample_rows", "aggregate", "search_data", "describe_dataset"):
            loop_warning = record_call(
                tool=name,
                dataset=dataset_arg,
                offset=arguments.get("offset", 0),
            )
        else:
            loop_warning = None

        if name == "index_local":
            result = await asyncio.to_thread(
                index_local,
                path=arguments["path"],
                name=arguments.get("name"),
                incremental=arguments.get("incremental", True),
                encoding=arguments.get("encoding"),
                delimiter=arguments.get("delimiter"),
                header_row=arguments.get("header_row", 0),
                sheet=arguments.get("sheet"),
                use_ai_summaries=arguments.get("use_ai_summaries", True),
                depth=arguments.get("depth", "standard"),
                storage_path=storage_path,
            )
        elif name == "index_repo":
            result = await index_repo(
                url=arguments["url"],
                incremental=arguments.get("incremental", True),
                github_token=arguments.get("github_token"),
                storage_path=storage_path,
            )
        elif name == "list_datasets":
            result = list_datasets(storage_path=storage_path)
        elif name == "list_repos":
            result = list_repos(storage_path=storage_path)

        elif name == "describe_dataset":
            result = describe_dataset(
                dataset=arguments["dataset"],
                columns=arguments.get("columns"),
                columns_offset=arguments.get("columns_offset", 0),
                storage_path=storage_path,
            )
        elif name == "describe_column":
            result = describe_column(
                dataset=arguments["dataset"],
                column=arguments["column"],
                top_n=arguments.get("top_n", 20),
                histogram_bins=arguments.get("histogram_bins", 10),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                storage_path=storage_path,
            )
        elif name == "search_data":
            result = search_data(
                dataset=arguments["dataset"],
                query=arguments["query"],
                search_scope=arguments.get("search_scope", "all"),
                max_results=arguments.get("max_results", 10),
                semantic=arguments.get("semantic", False),
                semantic_weight=arguments.get("semantic_weight", 0.5),
                semantic_only=arguments.get("semantic_only", False),
                storage_path=storage_path,
            )
        elif name == "get_rows":
            result = await asyncio.to_thread(
                get_rows,
                dataset=arguments["dataset"],
                filters=arguments.get("filters"),
                columns=arguments.get("columns"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "asc"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "aggregate":
            result = await asyncio.to_thread(
                aggregate,
                dataset=arguments["dataset"],
                aggregations=arguments["aggregations"],
                group_by=arguments.get("group_by"),
                filters=arguments.get("filters"),
                having=arguments.get("having"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "desc"),
                limit=arguments.get("limit", 50),
                approximate=arguments.get("approximate", False),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "sample_rows":
            result = await asyncio.to_thread(
                sample_rows,
                dataset=arguments["dataset"],
                n=arguments.get("n", 5),
                method=arguments.get("method", "head"),
                columns=arguments.get("columns"),
                seed=arguments.get("seed"),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "delete_dataset":
            result = delete_dataset(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_session_stats":
            result = get_session_stats(storage_path=storage_path)
        elif name == "get_schema_drift":
            result = get_schema_drift(
                dataset_a=arguments["dataset_a"],
                dataset_b=arguments["dataset_b"],
                storage_path=storage_path,
            )
        elif name == "get_data_hotspots":
            result = get_data_hotspots(
                dataset=arguments["dataset"],
                top_n=arguments.get("top_n", 10),
                include_runtime=arguments.get("include_runtime", True),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "get_correlations":
            result = await asyncio.to_thread(
                get_correlations,
                dataset=arguments["dataset"],
                min_abs_correlation=arguments.get("min_abs_correlation", 0.3),
                columns=arguments.get("columns"),
                top_n=arguments.get("top_n", 20),
                method=arguments.get("method", "pearson"),
                storage_path=storage_path,
            )
        elif name == "join_datasets":
            result = await asyncio.to_thread(
                join_datasets,
                dataset_a=arguments["dataset_a"],
                dataset_b=arguments["dataset_b"],
                join_column_a=arguments["join_column_a"],
                join_column_b=arguments["join_column_b"],
                join_type=arguments.get("join_type", "inner"),
                columns_a=arguments.get("columns_a"),
                columns_b=arguments.get("columns_b"),
                filters_a=arguments.get("filters_a"),
                filters_b=arguments.get("filters_b"),
                order_by=arguments.get("order_by"),
                order_dir=arguments.get("order_dir", "asc"),
                limit=arguments.get("limit", 50),
                offset=arguments.get("offset", 0),
                storage_path=storage_path,
            )
        elif name == "embed_dataset":
            result = await asyncio.to_thread(
                embed_dataset,
                dataset=arguments["dataset"],
                force=arguments.get("force", False),
                storage_path=storage_path,
            )
        elif name == "summarize_dataset":
            result = summarize_dataset_tool(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "validate_index":
            result = validate_index(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_dataset_history":
            result = get_dataset_history(
                dataset=arguments["dataset"],
                n=arguments.get("n", 10),
                storage_path=storage_path,
            )
        elif name == "get_dataset_health":
            result = get_dataset_health(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "suggest_keys":
            result = suggest_keys(
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "suggest_joins":
            result = await asyncio.to_thread(
                suggest_joins,
                dataset=arguments["dataset"],
                storage_path=storage_path,
            )
        elif name == "get_distribution":
            result = await asyncio.to_thread(
                get_distribution,
                dataset=arguments["dataset"],
                column=arguments["column"],
                bins=arguments.get("bins", 20),
                storage_path=storage_path,
            )
        elif name == "plan_query":
            result = plan_query(
                dataset=arguments["dataset"],
                intent=arguments.get("intent", "summarize"),
                storage_path=storage_path,
            )
        elif name == "run_sql":
            result = await asyncio.to_thread(
                run_sql,
                sql=arguments["sql"],
                datasets=arguments["datasets"],
                limit=arguments.get("limit", 500),
                redact=arguments.get("redact", True),
                redact_patterns=arguments.get("redact_patterns"),
                redact_skip_columns=arguments.get("redact_skip_columns"),
                storage_path=storage_path,
            )
        elif name == "get_schema_impact":
            result = await asyncio.to_thread(
                get_schema_impact,
                dataset_id=arguments["dataset_id"],
                column=arguments["column"],
                kind=arguments.get("kind", "drop_column"),
                new_name=arguments.get("new_name"),
                new_type=arguments.get("new_type"),
                max_depth=arguments.get("max_depth", 3),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "check_column_drop_safe":
            result = await asyncio.to_thread(
                check_column_drop_safe,
                dataset_id=arguments["dataset_id"],
                column=arguments["column"],
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "find_unused_columns":
            result = await asyncio.to_thread(
                find_unused_columns,
                dataset_id=arguments["dataset_id"],
                window_days=arguments.get("window_days", 30),
                min_calls=arguments.get("min_calls", 0),
                exclude_pk=arguments.get("exclude_pk", True),
                exclude_audit=arguments.get("exclude_audit", True),
                storage_path=storage_path,
            )
        elif name == "ingest_sql_log":
            result = await asyncio.to_thread(
                ingest_sql_log_file,
                file_path=arguments["file_path"],
                source=arguments.get("source", "auto"),
                redact=arguments.get("redact", True),
                max_rows=arguments.get("max_rows", 100000),
                storage_path=storage_path,
            )
        elif name == "data_health_radar":
            result = await asyncio.to_thread(
                data_health_radar,
                dataset=arguments["dataset"],
                include_runtime=arguments.get("include_runtime", True),
                window_days=arguments.get("window_days", 30),
                storage_path=storage_path,
            )
        elif name == "diff_data_health_radar":
            result = diff_data_health_radar(
                baseline=arguments["baseline"],
                current=arguments["current"],
            )
        elif name == "get_redaction_log":
            result = await asyncio.to_thread(
                get_redaction_log,
                dataset_id=arguments["dataset_id"],
                source=arguments.get("source"),
                since_days=arguments.get("since_days", 30),
                storage_path=storage_path,
            )
        else:
            result = {"error": f"Unknown tool: {name}"}

        if isinstance(result, dict) and "error" not in result:
            result = enforce_budget(result, name)
            if loop_warning:
                result.setdefault("_meta", {})["loop_warning"] = loop_warning

        if isinstance(result, dict):
            result.setdefault("_meta", {})["powered_by"] = (
                "jdatamunch-mcp by jgravelle · https://github.com/jgravelle/jdatamunch-mcp"
            )

            # meta_fields filtering (matches jcodemunch-mcp behaviour)
            from .config import get_meta_fields
            meta_fields = get_meta_fields()
            if meta_fields == []:
                result.pop("_meta", None)
            elif isinstance(meta_fields, list):
                existing_meta = result.pop("_meta", {})
                _meta: dict = {}
                if "powered_by" in meta_fields:
                    _meta["powered_by"] = existing_meta.get("powered_by", "")
                for field in meta_fields:
                    if field in existing_meta:
                        _meta[field] = existing_meta[field]
                if _meta:
                    result["_meta"] = _meta

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    except Exception as e:
        print(traceback.format_exc(), file=sys.stderr)
        return [TextContent(type="text", text=json.dumps({"error": str(e)}, indent=2))]


async def run_server():
    """Run the MCP server."""
    from jdatamunch_mcp import __version__
    from mcp.server.stdio import stdio_server

    print(
        f"jdatamunch-mcp {__version__} by jgravelle · https://github.com/jgravelle/jdatamunch-mcp",
        file=sys.stderr,
    )
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


def main(argv: Optional[list] = None):
    """Main entry point."""
    from .security import verify_package_integrity
    verify_package_integrity()

    parser = argparse.ArgumentParser(
        prog="jdatamunch-mcp",
        description="Run the jDataMunch MCP stdio server.",
    )
    parser.parse_args(argv)
    asyncio.run(run_server())


if __name__ == "__main__":
    main()
