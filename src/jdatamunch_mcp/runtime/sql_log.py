"""SQL log parser — pg_stat_statements CSV + generic JSONL.

Two input shapes are supported in v1.6.0:

* **pg_stat_statements CSV** — header autodetect. Common column aliases
  (``total_time`` / ``total_exec_time`` and ``mean_time`` /
  ``mean_exec_time``) are recognised so both 13.x and 14.x exports
  parse without flags.
* **Generic JSON-Lines** — one record per line with at minimum a
  ``query`` field; ``calls`` / ``total_time_ms`` / ``mean_time_ms`` are
  optional. Also accepts a top-level JSON array.

Transparent ``.gz`` support — open(.gz) just works on both shapes.

Per record we extract:

* the (redacted) query *fingerprint* — used as the rollup key
* ``table_ref`` — single table this fingerprint touched, one row per
  (fingerprint, table) pair
* ``column_ref`` — column identifier observed in SELECT / WHERE /
  ON / GROUP BY / ORDER BY / HAVING, one row per (fingerprint, table,
  column) triple

The parser is **pure** — no I/O against the dataset index, no
redaction. The orchestrator in :mod:`runtime.ingest` does that.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Optional


# --------------------------------------------------------------------------- #
# Data shape                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class SqlQueryRecord:
    """One parsed query line. ``query`` is *raw* — redact downstream."""
    query: str
    calls: int = 1
    total_time_ms: Optional[float] = None
    mean_time_ms: Optional[float] = None
    tables: list[str] = field(default_factory=list)
    columns_by_table: dict[str, list[str]] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Header aliasing                                                             #
# --------------------------------------------------------------------------- #

_QUERY_KEYS = ("query", "queryid", "statement", "sql")
_CALLS_KEYS = ("calls", "executions", "exec_count")
_TOTAL_TIME_KEYS = ("total_time", "total_exec_time", "total_time_ms", "total_exec_time_ms")
_MEAN_TIME_KEYS = ("mean_time", "mean_exec_time", "mean_time_ms", "mean_exec_time_ms")


def _pick(row: dict, keys: Iterable[str]) -> Optional[str]:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
        # case-insensitive fallback
        for rk in row.keys():
            if rk.lower() == k:
                v = row[rk]
                if v not in (None, ""):
                    return v
    return None


def _float_or_none(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_default(v, default: int = 1) -> int:
    if v is None or v == "":
        return default
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- #
# Table + column extraction                                                   #
# --------------------------------------------------------------------------- #
#
# Regex-based — purposely permissive. We'd rather over-detect table names
# than miss them; the resolver downstream filters by "is this table even
# indexed?" so noise drops out at the next stage.

# A SQL identifier: optionally schema-qualified, optionally quoted. We
# return the *trailing* identifier (the table or column name proper).
_IDENT = r"(?:\"[^\"]+\"|`[^`]+`|\[[^\]]+\]|[A-Za-z_][A-Za-z0-9_]*)"
_QUALIFIED = rf"(?:{_IDENT}\.)?{_IDENT}"

_TABLE_PATTERNS = (
    re.compile(rf"\bFROM\s+({_QUALIFIED})", re.IGNORECASE),
    re.compile(rf"\bJOIN\s+({_QUALIFIED})", re.IGNORECASE),
    re.compile(rf"\bUPDATE\s+({_QUALIFIED})", re.IGNORECASE),
    re.compile(rf"\bINSERT\s+INTO\s+({_QUALIFIED})", re.IGNORECASE),
    re.compile(rf"\bDELETE\s+FROM\s+({_QUALIFIED})", re.IGNORECASE),
    re.compile(rf"\bMERGE\s+INTO\s+({_QUALIFIED})", re.IGNORECASE),
)

# Column refs: we only mine the clauses that legitimately contain
# column identifiers. The SELECT list is captured separately because
# it can contain expressions and star-projections we want to skip.
_SELECT_CLAUSE_RE = re.compile(r"\bSELECT\b(.+?)\bFROM\b", re.IGNORECASE | re.DOTALL)
_WHERE_CLAUSE_RE = re.compile(
    r"\b(?:WHERE|ON|GROUP\s+BY|ORDER\s+BY|HAVING)\b(.*?)(?:\bGROUP\s+BY\b|\bORDER\s+BY\b|\bHAVING\b|\bLIMIT\b|\bOFFSET\b|;|\Z)",
    re.IGNORECASE | re.DOTALL,
)

# Inside a clause, pull column-shaped tokens. Accept ``alias.col`` and
# bare ``col``, but reject SQL keywords + standalone numerics.
_COL_TOKEN_RE = re.compile(rf"({_QUALIFIED})")

_KEYWORDS = frozenset({
    "select", "from", "where", "and", "or", "not", "in", "is", "null",
    "like", "ilike", "between", "as", "on", "group", "by", "order",
    "having", "limit", "offset", "asc", "desc", "case", "when", "then",
    "else", "end", "exists", "any", "all", "true", "false", "join",
    "left", "right", "inner", "outer", "cross", "natural", "using",
    "distinct", "union", "intersect", "except", "with", "values",
    "into", "set", "update", "insert", "delete", "merge", "returning",
    "count", "sum", "avg", "min", "max", "coalesce", "nullif", "cast",
})


def _strip_quotes(ident: str) -> str:
    if len(ident) >= 2 and ident[0] in '"`[' and ident[-1] in '"`]':
        return ident[1:-1]
    return ident


def _trailing_ident(qualified: str) -> str:
    """For ``schema.table`` return ``table``; for ``table`` return itself."""
    if "." in qualified:
        return _strip_quotes(qualified.rsplit(".", 1)[-1])
    return _strip_quotes(qualified)


def _is_col_token(tok: str) -> bool:
    """Filter out keywords, numerics, and obvious non-column tokens."""
    bare = _trailing_ident(tok).lower()
    if not bare or bare in _KEYWORDS:
        return False
    if bare.isdigit():
        return False
    # Function-call leftovers like ``count(*)`` — the COL regex won't
    # catch the ``*`` but might still capture ``count``. Strip those.
    if bare in {"count", "sum", "avg", "min", "max", "coalesce", "nullif", "cast"}:
        return False
    return True


def extract_tables(query: str) -> list[str]:
    """Return distinct table names referenced in the query, lower-cased."""
    found: list[str] = []
    seen: set[str] = set()
    for rx in _TABLE_PATTERNS:
        for m in rx.finditer(query):
            ident = _trailing_ident(m.group(1)).lower()
            if not ident or ident in seen or ident in _KEYWORDS:
                continue
            seen.add(ident)
            found.append(ident)
    return found


def extract_columns(query: str, tables: list[str]) -> dict[str, list[str]]:
    """Map each referenced table to a list of column names observed.

    Heuristic: we cannot reliably attribute every column to a specific
    table without a real SQL parser. Instead, we collect every distinct
    column-shaped token and emit it under *every* observed table. The
    resolver downstream filters by ``(table, column)`` membership in
    the dataset's schema, so a column observed in the query but absent
    from a dataset's schema simply doesn't get persisted for that
    dataset. This trades parser complexity for upfront over-emission.
    """
    if not tables:
        return {}

    columns: set[str] = set()
    # SELECT clause
    m = _SELECT_CLAUSE_RE.search(query)
    if m:
        clause = m.group(1)
        for tok_m in _COL_TOKEN_RE.finditer(clause):
            tok = tok_m.group(1)
            if _is_col_token(tok):
                columns.add(_trailing_ident(tok).lower())

    # WHERE / ON / GROUP BY / ORDER BY / HAVING
    for m in _WHERE_CLAUSE_RE.finditer(query):
        clause = m.group(1)
        for tok_m in _COL_TOKEN_RE.finditer(clause):
            tok = tok_m.group(1)
            if _is_col_token(tok):
                columns.add(_trailing_ident(tok).lower())

    # Drop tokens that match a table name — they're table refs, not column refs.
    table_set = set(tables)
    columns -= table_set

    if not columns:
        return {t: [] for t in tables}
    cols = sorted(columns)
    return {t: list(cols) for t in tables}


# --------------------------------------------------------------------------- #
# File parsing                                                                #
# --------------------------------------------------------------------------- #

def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8", errors="replace")  # type: ignore[return-value]
    return open(path, mode="r", encoding="utf-8", errors="replace")


def _iter_csv_rows(path: Path) -> Iterator[dict]:
    with _open_text(path) as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            yield row


def _iter_jsonl_rows(path: Path) -> Iterator[dict]:
    with _open_text(path) as fh:
        first = fh.read(1)
        if not first:
            return
        if first == "[":
            # Top-level array. Slurp it.
            rest = fh.read()
            try:
                data = json.loads("[" + rest)
            except json.JSONDecodeError:
                return
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict):
                        yield item
            return
        # JSONL — re-stream including the first char.
        fh.seek(0)
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def parse_sql_log_file(
    file_path: str,
    *,
    source: str = "auto",
    max_rows: int = 100_000,
) -> Iterator[SqlQueryRecord]:
    """Yield :class:`SqlQueryRecord` for each row in a SQL log file.

    Args:
        file_path: Path to a CSV / JSONL / .gz file.
        source: ``"pg_stat_statements"``, ``"jsonl"``, or ``"auto"``
            (the default — sniff by extension; ``.csv`` → pg_stat,
            anything else → jsonl).
        max_rows: Hard cap. Parser stops yielding after this many.

    The query string is returned **raw** — the orchestrator redacts.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"SQL log not found: {file_path}")

    if source == "auto":
        suffix = path.suffix.lower()
        if suffix == ".gz":
            suffix = path.with_suffix("").suffix.lower()
        source = "pg_stat_statements" if suffix == ".csv" else "jsonl"

    if source == "pg_stat_statements":
        rows_iter: Iterator[dict] = _iter_csv_rows(path)
    elif source == "jsonl":
        rows_iter = _iter_jsonl_rows(path)
    else:
        raise ValueError(f"Unsupported source: {source!r}")

    count = 0
    for row in rows_iter:
        if count >= max_rows:
            break
        q = _pick(row, _QUERY_KEYS)
        if not q or not isinstance(q, str):
            continue
        tables = extract_tables(q)
        columns_by_table = extract_columns(q, tables) if tables else {}
        rec = SqlQueryRecord(
            query=q,
            calls=_int_or_default(_pick(row, _CALLS_KEYS), default=1),
            total_time_ms=_float_or_none(_pick(row, _TOTAL_TIME_KEYS)),
            mean_time_ms=_float_or_none(_pick(row, _MEAN_TIME_KEYS)),
            tables=tables,
            columns_by_table=columns_by_table,
        )
        yield rec
        count += 1
