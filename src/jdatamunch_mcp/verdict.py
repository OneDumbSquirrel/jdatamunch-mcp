"""Retrieval verdict — suite-parity honesty contract (jData side).

Mirrors the agent-facing ``_meta.verdict`` that jCodeMunch and jDocMunch emit on
their search tools: an empty column search is positive, token-saving evidence —
the index can attest "no column matches this" where a nearest-neighbour search
always returns its closest something. Clean-room jData implementation (no
cross-suite import); only the wire shape is shared.

jData search scores are rank-normalized (the top hit is always 1.0), so there is
no calibrated confidence signal to threshold. This tool therefore emits
``ok`` / ``absent`` / ``degraded`` only — no ``low_confidence`` (an honest
divergence from the jDoc/jcm search tools, which do carry a confidence metric).
``degraded`` fires when semantic search was requested but the embedding channel
fell back to keyword-only.
"""

from __future__ import annotations

from typing import Optional, Sequence

STATE_OK = "ok"
STATE_ABSENT = "absent"
STATE_DEGRADED = "degraded"

_NOTES = {
    STATE_OK: "Confident matches returned.",
    STATE_ABSENT: (
        "No column matched after scanning the dataset. Treat this as strong "
        "evidence no such column/value is present; do not reformulate the same "
        "query expecting a hit."
    ),
    STATE_DEGRADED: (
        "Semantic search was requested but the embedding channel was "
        "unavailable, so results are keyword-only and absence is NOT proven. "
        "Configure an embedding provider (or run embed_dataset) for semantic "
        "recall."
    ),
}


def suggest_columns(
    query: str,
    columns: Optional[Sequence[dict]],
    cap: int = 5,
) -> list:
    """Column names containing a query term (near-misses).

    Returned on an absent verdict so the agent can redirect instead of retrying
    the same empty query.
    """
    terms = [t for t in (query or "").lower().split() if len(t) >= 3]
    if not terms or not columns:
        return []
    out: list = []
    seen: set = set()
    for col in columns:
        name = str(col.get("name", ""))
        name_lower = name.lower()
        if name and name not in seen and any(t in name_lower for t in terms):
            seen.add(name)
            out.append(name)
            if len(out) >= cap:
                break
    return out


def build_verdict(
    *,
    result_count: int,
    semantic_requested: bool = False,
    semantic_available: bool = True,
    lexical_used: bool = True,
    did_you_mean: Optional[Sequence[str]] = None,
) -> dict:
    """Compute the ``_meta.verdict`` dict for a column search.

    ``degraded`` takes precedence over ``absent``: a downgraded channel means a
    partial scan, which cannot prove absence.
    """
    if semantic_requested and not semantic_available:
        state = STATE_DEGRADED
    elif result_count == 0:
        state = STATE_ABSENT
    else:
        state = STATE_OK

    if semantic_requested and not semantic_available:
        semantic_channel = "unavailable"
    elif semantic_requested:
        semantic_channel = "ok"
    else:
        semantic_channel = "off"

    verdict = {
        "state": state,
        "channels": {
            "lexical": "ok" if lexical_used else "off",
            "semantic": semantic_channel,
        },
        "note": _NOTES[state],
    }
    if did_you_mean:
        verdict["did_you_mean"] = list(did_you_mean)[:5]
    return verdict
