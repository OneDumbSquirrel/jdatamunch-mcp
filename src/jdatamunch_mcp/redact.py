"""Cell-level redaction for tabular output.

Tabular data routinely contains PII and credentials in raw cells: an analytics
CSV with an `email` column, a billing extract with credit-card numbers, a
config dump with API tokens. When an agent calls get_rows / sample_rows /
run_sql / aggregate / describe_column, those cells flow straight into LLM
context — where they may be cached, logged, or reflected to a tool further
downstream.

This module scrubs cells before they leave the tool. The default policy is
ON for bulk-output tools; callers who need raw data (legitimate analytics on
data they own) opt out per call with ``redact=False``.

Public API:
  * ``redact_rows(rows, ...)``           — list[dict] (get_rows / sample_rows / run_sql)
  * ``redact_value_distribution(items, ...)`` — [{value, count, pct}, ...]
  * ``redact_aggregate_result(result, ...)``  — query_aggregate response in place
  * ``redaction_summary(...)``            — assemble the response-side meta block

Returned counts are surfaced to the agent so it can decide whether to re-run
with ``redact=False`` if a real value got scrubbed.
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Optional

# --------------------------------------------------------------------------- #
# Pattern registry                                                            #
# --------------------------------------------------------------------------- #
# Order matters: longer / more-specific patterns first so a credential block
# isn't broken up by a generic API-key match.

_PATTERNS: list[tuple[str, "re.Pattern[str]"]] = [
    # Multi-line PEM bodies — match the whole block, not pieces of it.
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
            r"[\s\S]*?-----END[^-]*?PRIVATE KEY-----",
        ),
    ),
    # AWS access key id — fixed 20-char prefix
    ("aws_access_key", re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASCA)[0-9A-Z]{16}\b")),
    # GitHub fine-grained / classic PAT
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,255}\b")),
    # Slack tokens
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    # JWT — 3 base64url segments separated by dots, header starts `eyJ`
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")),
    # Stripe-style live/test keys and common secret-key conventions
    ("api_key_prefixed", re.compile(r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b")),
    # OpenAI-style sk-... keys (broad — only matches the explicit `sk-` prefix)
    ("api_key_openai", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    # US Social Security Number
    ("ssn", re.compile(r"\b(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}\b")),
    # Credit card — broad 13-19 digit sequence (Luhn-validated post-match)
    ("credit_card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    # Email address
    ("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}\b")),
]

# Patterns that ship enabled by default. Callers can override by passing
# `enabled_patterns=...`. Email is included because tabular PII most commonly
# is an email column.
DEFAULT_ENABLED: frozenset[str] = frozenset(
    {
        "private_key",
        "aws_access_key",
        "github_pat",
        "slack_token",
        "jwt",
        "api_key_prefixed",
        "api_key_openai",
        "ssn",
        "credit_card",
        "email",
    }
)

KNOWN_PATTERN_NAMES: frozenset[str] = frozenset(name for name, _ in _PATTERNS)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #

def _luhn_ok(s: str) -> bool:
    digits = [int(c) for c in s if c.isdigit()]
    if len(digits) < 13 or len(digits) > 19:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def _compile_custom(patterns: Iterable[str]) -> list[tuple[str, "re.Pattern[str]"]]:
    out: list[tuple[str, "re.Pattern[str]"]] = []
    for i, pat in enumerate(patterns):
        try:
            out.append((f"custom_{i}", re.compile(pat)))
        except re.error:
            # Silent skip — surfaced via _meta.redaction.invalid_patterns
            out.append((f"__invalid_{i}", None))  # type: ignore[arg-type]
    return out


def _active_patterns(
    enabled: Optional[Iterable[str]],
    custom: Optional[Iterable[str]],
) -> tuple[list[tuple[str, "re.Pattern[str]"]], list[int]]:
    """Return (active_patterns, invalid_custom_indexes)."""
    en = set(enabled) if enabled is not None else set(DEFAULT_ENABLED)
    active = [(k, rx) for (k, rx) in _PATTERNS if k in en]
    invalid: list[int] = []
    if custom:
        compiled = _compile_custom(list(custom))
        for (name, rx) in compiled:
            if rx is None:
                invalid.append(int(name.split("_")[-1]))
            else:
                active.append((name, rx))
    return active, invalid


def _redact_str(
    s: str,
    active: list[tuple[str, "re.Pattern[str]"]],
    replacement_fmt: str,
) -> tuple[str, list[str]]:
    """Apply each active pattern in turn. Returns (new_string, kinds_hit)."""
    kinds_hit: list[str] = []
    out = s
    for kind, rx in active:
        def _sub(m: "re.Match[str]") -> str:
            txt = m.group(0)
            if kind == "credit_card" and not _luhn_ok(txt):
                return txt
            kinds_hit.append(kind)
            return replacement_fmt.format(kind=kind)

        out = rx.sub(_sub, out)
    return out, kinds_hit


def _redact_cell(
    val: Any,
    active: list[tuple[str, "re.Pattern[str]"]],
    replacement_fmt: str,
) -> tuple[Any, list[str]]:
    """Redact a single cell value. Leaves non-strings untouched (numbers, None)."""
    if val is None:
        return val, []
    if not isinstance(val, str):
        # Numbers can match SSN-like / credit-card patterns when stringified.
        # We only redact actual strings — agents rarely treat numeric columns as PII.
        return val, []
    new_s, hits = _redact_str(val, active, replacement_fmt)
    return (new_s if hits else val), hits


# --------------------------------------------------------------------------- #
# Public API — row redaction                                                  #
# --------------------------------------------------------------------------- #

def redact_rows(
    rows: list[dict],
    *,
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
    replacement: str = "[REDACTED:{kind}]",
    skip_columns: Optional[Iterable[str]] = None,
) -> tuple[list[dict], dict]:
    """Redact PII / credentials from a list of row dicts.

    ``skip_columns`` lets callers exempt columns they know are non-sensitive
    (e.g. a deliberate `email_hashed` column) — useful when the default email
    pattern would otherwise scrub a legitimate column.

    Returns ``(new_rows, summary)`` where summary is::

        {"cells_redacted": int, "patterns_matched": {kind: count, ...}}
    """
    active, invalid = _active_patterns(enabled_patterns, custom_patterns)
    skip = set(skip_columns or ())
    pattern_counts: dict[str, int] = {}
    cells_redacted = 0

    out_rows: list[dict] = []
    for row in rows:
        new_row: dict = {}
        for k, v in row.items():
            if k in skip:
                new_row[k] = v
                continue
            new_v, hits = _redact_cell(v, active, replacement)
            if hits:
                cells_redacted += 1
                for kind in hits:
                    pattern_counts[kind] = pattern_counts.get(kind, 0) + 1
            new_row[k] = new_v
        out_rows.append(new_row)

    summary: dict = {
        "cells_redacted": cells_redacted,
        "patterns_matched": pattern_counts,
    }
    if invalid:
        summary["invalid_custom_patterns"] = invalid
    return out_rows, summary


def redact_value_distribution(
    items: list[dict],
    *,
    value_key: str = "value",
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
    replacement: str = "[REDACTED:{kind}]",
) -> tuple[list[dict], dict]:
    """Redact the ``value_key`` field of a list of ``{value, count, pct}`` items.

    Used by describe_column.value_distribution / top_values where the raw
    top-N value list can leak per-cell PII even though row data is summarised.
    """
    active, invalid = _active_patterns(enabled_patterns, custom_patterns)
    pattern_counts: dict[str, int] = {}
    cells_redacted = 0
    out: list[dict] = []
    for entry in items:
        new_entry = dict(entry)
        v = new_entry.get(value_key)
        new_v, hits = _redact_cell(v, active, replacement)
        if hits:
            cells_redacted += 1
            for kind in hits:
                pattern_counts[kind] = pattern_counts.get(kind, 0) + 1
        new_entry[value_key] = new_v
        out.append(new_entry)
    summary: dict = {
        "cells_redacted": cells_redacted,
        "patterns_matched": pattern_counts,
    }
    if invalid:
        summary["invalid_custom_patterns"] = invalid
    return out, summary


def redact_scalar_list(
    items: list,
    *,
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
    replacement: str = "[REDACTED:{kind}]",
) -> tuple[list, dict]:
    """Redact a flat list of scalars (e.g. ``sample_values``)."""
    active, invalid = _active_patterns(enabled_patterns, custom_patterns)
    pattern_counts: dict[str, int] = {}
    cells_redacted = 0
    out: list = []
    for v in items:
        new_v, hits = _redact_cell(v, active, replacement)
        if hits:
            cells_redacted += 1
            for kind in hits:
                pattern_counts[kind] = pattern_counts.get(kind, 0) + 1
        out.append(new_v)
    summary: dict = {
        "cells_redacted": cells_redacted,
        "patterns_matched": pattern_counts,
    }
    if invalid:
        summary["invalid_custom_patterns"] = invalid
    return out, summary


def merge_summary(*summaries: dict) -> dict:
    """Combine multiple per-pass summaries into a single response-side block."""
    cells = 0
    patterns: dict[str, int] = {}
    invalid: list = []
    for s in summaries:
        if not s:
            continue
        cells += int(s.get("cells_redacted", 0))
        for k, v in (s.get("patterns_matched") or {}).items():
            patterns[k] = patterns.get(k, 0) + int(v)
        inv = s.get("invalid_custom_patterns") or []
        for idx in inv:
            if idx not in invalid:
                invalid.append(idx)
    out: dict = {"cells_redacted": cells, "patterns_matched": patterns}
    if invalid:
        out["invalid_custom_patterns"] = invalid
    return out


# --------------------------------------------------------------------------- #
# Trace-level redaction (v1.6.0 — runtime ingest)                             #
# --------------------------------------------------------------------------- #
# Cell-level redaction scrubs PII from one column's worth of values. Trace
# redaction scrubs PII from free-form text — SQL query bodies or log messages
# — by stripping the dynamic parts (string literals, numeric literals) AND
# applying the cell registry to whatever remains. Designed for the runtime
# ingest pipeline (v1.6.0 ingest_sql_log) where queries arrive as raw text
# that may contain embedded credentials, emails, or PII in WHERE clauses.

# Standard SQL single-quoted string literal. Handles both backslash escapes
# (``\'``) and SQL-standard doubled single quotes (``''``).
_SQL_STRING_LIT_RE = re.compile(r"'(?:[^'\\]|\\.|'')*'")

# Numeric literal: int, decimal, or scientific notation. Word-boundary on
# each side keeps us out of identifiers like ``col_123``.
_SQL_NUMERIC_LIT_RE = re.compile(r"\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b")

# IPv4 — added for trace messages (log lines), not used by SQL.
_IPV4_RE = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d?\d)\b"
)


def redact_sql_query_text(
    query: str,
    *,
    scrub_string_literals: bool = True,
    scrub_numeric_literals: bool = True,
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
    replacement: str = "[REDACTED:{kind}]",
) -> tuple[str, dict]:
    """Scrub a SQL query body for safe storage in the runtime trace tables.

    Strategy:
      1. Replace ``'literal'`` and ``"literal"`` string contents with ``?``
         so the query *shape* survives but the values don't. Counts each
         under ``sql_string_literal``.
      2. Replace numeric literals (``42``, ``3.14``, ``1.5e-3``) with ``?``.
         Counts under ``sql_numeric_literal``.
      3. Run the cell-PII registry on the remainder, catching tokens that
         survive (e.g. an API key concatenated into an identifier).

    Returns ``(redacted_query, summary)``. Summary keys match the cell
    redactors: ``cells_redacted`` (total substitution count) and
    ``patterns_matched`` (per-kind counts).
    """
    out = query
    pattern_counts: dict[str, int] = {}
    cells_redacted = 0

    if scrub_string_literals:
        def _str_sub(m: "re.Match[str]") -> str:
            nonlocal cells_redacted
            pattern_counts["sql_string_literal"] = pattern_counts.get("sql_string_literal", 0) + 1
            cells_redacted += 1
            return "'?'"
        out = _SQL_STRING_LIT_RE.sub(_str_sub, out)

    if scrub_numeric_literals:
        def _num_sub(m: "re.Match[str]") -> str:
            nonlocal cells_redacted
            pattern_counts["sql_numeric_literal"] = pattern_counts.get("sql_numeric_literal", 0) + 1
            cells_redacted += 1
            return "?"
        out = _SQL_NUMERIC_LIT_RE.sub(_num_sub, out)

    # Cell registry on what survives. Default to a tight set — credit_card
    # is intentionally OFF for SQL-text because Luhn-valid 13–19 digit
    # sequences inside arbitrary tokens are nearly always false positives
    # once literals are already scrubbed.
    en_default = (DEFAULT_ENABLED - {"credit_card"}) if enabled_patterns is None else enabled_patterns
    active, invalid = _active_patterns(en_default, custom_patterns)
    out, kinds_hit = _redact_str(out, active, replacement)
    for kind in kinds_hit:
        pattern_counts[kind] = pattern_counts.get(kind, 0) + 1
    cells_redacted += len(kinds_hit)

    summary: dict = {
        "cells_redacted": cells_redacted,
        "patterns_matched": pattern_counts,
    }
    if invalid:
        summary["invalid_custom_patterns"] = invalid
    return out, summary


def redact_trace_message(
    text: str,
    *,
    scrub_ipv4: bool = True,
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
    replacement: str = "[REDACTED:{kind}]",
) -> tuple[str, dict]:
    """Scrub a free-form trace / log message.

    Applies the cell-PII registry plus an optional IPv4 sweep. Use this
    for stack-frame messages, generic log lines, or any non-SQL text
    arriving at the ingest chokepoint.
    """
    out = text
    pattern_counts: dict[str, int] = {}
    cells_redacted = 0

    if scrub_ipv4:
        def _ipv4_sub(m: "re.Match[str]") -> str:
            nonlocal cells_redacted
            pattern_counts["ipv4"] = pattern_counts.get("ipv4", 0) + 1
            cells_redacted += 1
            return replacement.format(kind="ipv4")
        out = _IPV4_RE.sub(_ipv4_sub, out)

    active, invalid = _active_patterns(enabled_patterns, custom_patterns)
    out, kinds_hit = _redact_str(out, active, replacement)
    for kind in kinds_hit:
        pattern_counts[kind] = pattern_counts.get(kind, 0) + 1
    cells_redacted += len(kinds_hit)

    summary: dict = {
        "cells_redacted": cells_redacted,
        "patterns_matched": pattern_counts,
    }
    if invalid:
        summary["invalid_custom_patterns"] = invalid
    return out, summary


def redaction_meta(
    *,
    applied: bool,
    summary: Optional[dict] = None,
    enabled_patterns: Optional[Iterable[str]] = None,
    custom_patterns: Optional[Iterable[str]] = None,
) -> dict:
    """Assemble the ``_meta["redaction"]`` block for a tool response.

    ``applied=False`` (caller opted out) still emits the block so the absence
    of redaction is auditable from the wire.
    """
    block: dict = {"applied": applied}
    if applied and summary is not None:
        block["cells_redacted"] = int(summary.get("cells_redacted", 0))
        pm = summary.get("patterns_matched") or {}
        if pm:
            block["patterns_matched"] = pm
        inv = summary.get("invalid_custom_patterns") or []
        if inv:
            block["invalid_custom_patterns"] = inv
    if enabled_patterns is not None:
        block["enabled_patterns"] = sorted(set(enabled_patterns))
    if custom_patterns:
        block["custom_patterns_count"] = len(list(custom_patterns))
    return block
