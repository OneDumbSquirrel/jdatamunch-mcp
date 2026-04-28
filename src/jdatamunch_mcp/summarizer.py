"""Rule-based natural-language summaries for datasets and columns.

Generates human-readable summaries from profiled statistics — no external
API calls required.  Summaries are stored in index.json and surfaced by
describe_dataset / describe_column.
"""

from typing import Any, Optional


# ---------------------------------------------------------------------------
# Column-level summaries
# ---------------------------------------------------------------------------

def _fmt_number(n: Any) -> str:
    """Format a number for display (compact large numbers)."""
    if n is None:
        return "?"
    if isinstance(n, float):
        if abs(n) >= 1_000_000:
            return f"{n:,.0f}"
        return f"{n:,.2f}" if n != int(n) else f"{int(n):,}"
    return f"{n:,}"


def _null_note(null_pct: float) -> str:
    if null_pct == 0:
        return ""
    if null_pct >= 50:
        return f" ({null_pct:.0f}% null — sparse)"
    if null_pct >= 10:
        return f" ({null_pct:.0f}% null)"
    if null_pct > 0:
        return f" ({null_pct:.1f}% null)"
    return ""


def _cardinality_label(card: int, count: int, is_unique: bool, is_pk: bool) -> str:
    """Describe cardinality in human terms."""
    if is_pk:
        return "unique identifier"
    if is_unique:
        return "all unique values"
    if card == 1:
        return "single constant value"
    if card == 2:
        return "binary (2 distinct values)"
    if card <= 10:
        return f"categorical ({card} distinct values)"
    if card <= 100:
        return f"low-cardinality ({card} distinct values)"
    ratio = card / count if count > 0 else 0
    if ratio > 0.9:
        return f"near-unique ({card:,} distinct in {count:,} rows)"
    if card <= 1_000:
        return f"moderate-cardinality ({card:,} distinct values)"
    return f"high-cardinality ({card:,} distinct values)"


def summarize_column(col: dict) -> str:
    """Generate a one-line natural-language summary for a column profile dict."""
    name = col["name"]
    ctype = col["type"]
    count = col.get("count", 0)
    null_pct = col.get("null_pct", 0)
    card = col.get("cardinality", 0)
    is_unique = col.get("is_unique", False)
    is_pk = col.get("is_primary_key_candidate", False)

    nulls = _null_note(null_pct)

    if ctype in ("integer", "float"):
        lo = col.get("min")
        hi = col.get("max")
        mean = col.get("mean")
        median = col.get("median")
        card_label = _cardinality_label(card, count + col.get("null_count", 0), is_unique, is_pk)

        parts = [f"{ctype.capitalize()} column"]
        if lo is not None and hi is not None:
            parts.append(f"ranging from {_fmt_number(lo)} to {_fmt_number(hi)}")
        if mean is not None:
            parts.append(f"mean {_fmt_number(mean)}")
        if median is not None:
            parts.append(f"median {_fmt_number(median)}")
        parts.append(card_label)
        return f"{'; '.join(parts)}.{nulls}"

    if ctype == "datetime":
        dt_min = col.get("datetime_min")
        dt_max = col.get("datetime_max")
        dt_fmt = col.get("datetime_format")
        parts = ["Datetime column"]
        if dt_min and dt_max:
            parts.append(f"spanning {dt_min} to {dt_max}")
        elif dt_min:
            parts.append(f"from {dt_min}")
        if dt_fmt:
            parts.append(f"format: {dt_fmt}")
        return f"{'; '.join(parts)}.{nulls}"

    # String type
    card_label = _cardinality_label(card, count + col.get("null_count", 0), is_unique, is_pk)
    top = col.get("top_values", [])
    top_preview = ""
    if top and card <= 10:
        vals = [str(t["value"]) for t in top[:5]]
        top_preview = f" Values: {', '.join(vals)}."

    return f"Text column; {card_label}.{nulls}{top_preview}"


# ---------------------------------------------------------------------------
# Dataset-level summary
# ---------------------------------------------------------------------------

def _classify_domain(columns: list[dict]) -> Optional[str]:
    """Coarse domain classification (C4): financial / temporal / geo / log / event.

    Heuristic only — driven by column name tokens and semantic types. Returns
    None when no domain has decisive evidence.
    """
    name_tokens: set = set()
    for c in columns:
        s = c["name"].lower()
        for ch in (" ", "-", ".", "/"):
            s = s.replace(ch, "_")
        name_tokens.update(t for t in s.split("_") if t)
    semantic = {c.get("semantic_type") for c in columns if c.get("semantic_type")}

    # Geo: lat/lon either by name or semantic type
    if "lat" in semantic or "lon" in semantic:
        return "geo"
    if {"latitude", "longitude"}.issubset(name_tokens):
        return "geo"
    if "zip_us" in semantic or "iso_country" in semantic:
        return "geo"

    # Financial
    fin = {"price", "amount", "currency", "cost", "revenue", "profit", "balance",
           "invoice", "tax", "fee", "salary", "payment", "usd", "eur"}
    if "iso_currency" in semantic or len(fin & name_tokens) >= 2:
        return "financial"

    # Log / event
    log = {"timestamp", "ts", "level", "severity", "logger", "trace", "span",
           "request_id", "session_id", "user_agent", "status_code"}
    if len(log & name_tokens) >= 2:
        return "log"

    # Event
    event = {"event", "events", "action", "verb", "object", "actor", "occurred"}
    if "event" in name_tokens or len(event & name_tokens) >= 2:
        return "event"

    # Temporal — at least one datetime column is present
    if any(c.get("type") == "datetime" for c in columns):
        return "temporal"

    return None


def _humanize_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    if n < 1024 * 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    return f"{n / (1024 * 1024 * 1024):.2f} GB"


def _pluralize(n: int, word: str) -> str:
    return f"{n:,} {word}" if n == 1 else f"{n:,} {word}s"


def summarize_dataset(
    dataset_id: str,
    columns: list[dict],
    row_count: int,
    source_format: str,
    source_size_bytes: int,
    source_path: Optional[str] = None,
) -> str:
    """Generate a multi-sentence natural-language summary for a dataset."""
    n_cols = len(columns)

    # Type breakdown
    type_counts: dict[str, int] = {}
    for c in columns:
        t = c.get("type", "string")
        type_counts[t] = type_counts.get(t, 0) + 1

    type_parts = []
    for t in ("integer", "float", "datetime", "string"):
        cnt = type_counts.get(t, 0)
        if cnt:
            type_parts.append(f"{cnt} {t}")

    # Opening sentence
    fmt_label = source_format.upper() if source_format in ("csv", "tsv") else source_format.capitalize()
    opening = (
        f"{fmt_label} dataset with {_pluralize(row_count, 'row')} and "
        f"{_pluralize(n_cols, 'column')} ({_humanize_bytes(source_size_bytes)})."
    )

    # Column type breakdown
    type_line = f"Column types: {', '.join(type_parts)}." if type_parts else ""

    # Key columns: primary key candidates
    pk_cols = [c["name"] for c in columns if c.get("is_primary_key_candidate")]
    pk_line = ""
    if pk_cols:
        if len(pk_cols) == 1:
            pk_line = f"Primary key candidate: {pk_cols[0]}."
        else:
            pk_line = f"Primary key candidates: {', '.join(pk_cols[:3])}."

    # Temporal range
    dt_cols = [c for c in columns if c.get("type") == "datetime"]
    temporal_line = ""
    if dt_cols:
        dt_col = dt_cols[0]
        dt_min = dt_col.get("datetime_min")
        dt_max = dt_col.get("datetime_max")
        if dt_min and dt_max:
            temporal_line = f"Temporal range ({dt_col['name']}): {dt_min} to {dt_max}."

    # Data quality notes
    quality_notes = []
    high_null_cols = [c["name"] for c in columns if c.get("null_pct", 0) >= 20]
    if high_null_cols:
        if len(high_null_cols) <= 3:
            quality_notes.append(f"High null rate in: {', '.join(high_null_cols)}.")
        else:
            quality_notes.append(f"{len(high_null_cols)} columns have >20% nulls.")

    constant_cols = [c["name"] for c in columns if c.get("cardinality", 0) == 1 and c.get("null_pct", 0) < 50]
    if constant_cols:
        if len(constant_cols) <= 3:
            quality_notes.append(f"Constant-value columns: {', '.join(constant_cols)}.")
        else:
            quality_notes.append(f"{len(constant_cols)} columns contain a single constant value.")

    quality_line = " ".join(quality_notes)

    # Domain classification (C4)
    domain = _classify_domain(columns)
    domain_line = f"Likely domain: {domain}." if domain else ""

    # Assemble
    parts = [opening, type_line, pk_line, temporal_line, domain_line, quality_line]
    return " ".join(p for p in parts if p).strip()
