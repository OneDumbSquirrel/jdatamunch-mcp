"""Embedding drift detector for jdatamunch-mcp.

Column embeddings power semantic `search_data` / `find_similar_columns`. The
embedding provider can change underneath a stored index silently: Gemini bumps
a model revision, OpenAI reweights a model under the same name, or a local
sentence-transformers model is swapped. When that happens the vectors stored at
index time no longer line up with what the live query encoder produces, and
semantic ranking quietly degrades.

This module pins a small *canary* -- 16 deterministic, data-flavored strings
embedded with the active provider -- and recomputes them on demand. Average
cosine drift between the captured and current vectors flags a change.

Storage: ``<index_path>/embed_canary.json`` with
``{provider, model, dim, captured_at, strings: [...], vectors: [[...]]}``.

Drift threshold (cosine distance ``1 - cos``, max across canaries) defaults to
0.05 -- comfortably above floating-point noise on a stable model.

Sibling of jcodemunch-mcp / jdocmunch-mcp ``check_embedding_drift``. The canary
strings here span tabular / column semantics (the domain jData embeds) rather
than code tokens.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .config import get_index_path

logger = logging.getLogger(__name__)

_CANARY_FILE = "embed_canary.json"
_DEFAULT_DRIFT_THRESHOLD = 0.05  # cosine distance; cosine sim < 0.95 alarms

# Sixteen short, semantically diverse strings spanning common column / data
# concepts (identifiers, money, dates, geo, categories, free text). Order is
# part of the canary contract -- never reorder; append only.
CANARY_STRINGS: tuple[str, ...] = (
    "customer email address",
    "total transaction amount in US dollars",
    "ISO 8601 timestamp",
    "primary key identifier column",
    "two-letter country code",
    "first and last name",
    "phone number with country prefix",
    "product SKU or catalog number",
    "latitude and longitude coordinates",
    "boolean active or inactive flag",
    "percentage value between 0 and 100",
    "free-text customer review",
    "categorical order status pending shipped delivered",
    "date of birth",
    "IPv4 network address",
    "monetary account balance with two decimal places",
)


def _canary_path(storage_path: Optional[str] = None) -> Path:
    root = get_index_path(storage_path)
    root.mkdir(parents=True, exist_ok=True)
    return root / _CANARY_FILE


def _resolve_provider() -> Optional[tuple[str, str]]:
    """Reuse the live encoder's provider detection so we never drift from it."""
    try:
        from .embeddings import detect_provider
        return detect_provider()
    except Exception:
        logger.debug("Failed to resolve embedding provider", exc_info=True)
        return None


def _embed(strings: list[str], provider: str, model: str) -> list[list[float]]:
    from .embeddings import embed_texts
    return embed_texts(strings, provider, model)


def _cosine(a: list[float], b: list[float]) -> float:
    from .embeddings import cosine_similarity
    if not a or not b or len(a) != len(b):
        return 0.0
    return cosine_similarity(a, b)


def capture_canary(storage_path: Optional[str] = None, *, force: bool = False) -> dict:
    """Embed CANARY_STRINGS with the active provider and persist them.

    When a canary already exists and ``force`` is False, returns the existing
    snapshot's metadata without re-embedding.
    """
    path = _canary_path(storage_path)
    if path.exists() and not force:
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            return {
                "captured": False,
                "reason": "canary_already_exists",
                "provider": existing.get("provider"),
                "model": existing.get("model"),
                "captured_at": existing.get("captured_at"),
                "dim": existing.get("dim"),
                "path": str(path),
            }
        except (json.JSONDecodeError, OSError, ValueError):
            logger.debug("Failed to read existing canary at %s", path, exc_info=True)
            # fall through to re-capture

    provider_info = _resolve_provider()
    if not provider_info:
        return {
            "captured": False,
            "error": (
                "No embedding provider configured. Set JDATAMUNCH_EMBED_MODEL "
                "(sentence-transformers, free/local), GOOGLE_API_KEY + "
                "GOOGLE_EMBED_MODEL (Gemini), or OPENAI_API_KEY + "
                "OPENAI_EMBED_MODEL (OpenAI)."
            ),
        }
    provider, model = provider_info
    try:
        vectors = _embed(list(CANARY_STRINGS), provider, model)
    except Exception as exc:
        return {
            "captured": False,
            "provider": provider,
            "model": model,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not vectors or any(not v for v in vectors):
        return {
            "captured": False,
            "provider": provider,
            "model": model,
            "error": "Provider returned an empty vector set",
        }
    dim = len(vectors[0])
    snapshot = {
        "provider": provider,
        "model": model,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "dim": dim,
        "strings": list(CANARY_STRINGS),
        "vectors": vectors,
    }
    path.write_text(json.dumps(snapshot), encoding="utf-8")
    return {
        "captured": True,
        "provider": provider,
        "model": model,
        "captured_at": snapshot["captured_at"],
        "dim": dim,
        "n_canaries": len(CANARY_STRINGS),
        "path": str(path),
    }


def check_drift(
    storage_path: Optional[str] = None,
    *,
    threshold: float = _DEFAULT_DRIFT_THRESHOLD,
) -> dict:
    """Recompute embeddings for the pinned canary and compare to the snapshot.

    Returns ``{has_canary, alarm, max_drift, mean_drift, threshold, per_canary,
    provider, model, captured_provider, captured_model, captured_at}``. If no
    canary exists, returns ``{has_canary: False}`` with a hint. If the live
    provider differs from the captured one, the diff is reported but the cosine
    comparison still runs (a provider swap is often exactly what alarms).
    """
    path = _canary_path(storage_path)
    if not path.exists():
        return {
            "has_canary": False,
            "hint": (
                "No canary pinned yet. Call check_embedding_drift(force=true) "
                "to capture a baseline first, then re-run after a suspected "
                "provider change."
            ),
        }
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        return {
            "has_canary": False,
            "error": f"Failed to read canary: {type(exc).__name__}: {exc}",
        }

    provider_info = _resolve_provider()
    if not provider_info:
        return {
            "has_canary": True,
            "captured_provider": snapshot.get("provider"),
            "captured_model": snapshot.get("model"),
            "error": "No embedding provider configured at check time.",
        }
    cur_provider, cur_model = provider_info

    saved_strings = snapshot.get("strings") or list(CANARY_STRINGS)
    saved_vectors = snapshot.get("vectors") or []
    try:
        cur_vectors = _embed(saved_strings, cur_provider, cur_model)
    except Exception as exc:
        return {
            "has_canary": True,
            "captured_provider": snapshot.get("provider"),
            "captured_model": snapshot.get("model"),
            "provider": cur_provider,
            "model": cur_model,
            "error": f"Re-embedding failed: {type(exc).__name__}: {exc}",
        }

    per_canary: list[dict] = []
    drifts: list[float] = []
    for i, (text, saved, cur) in enumerate(zip(saved_strings, saved_vectors, cur_vectors)):
        if not saved or not cur:
            continue
        sim = _cosine(saved, cur)
        drift = round(1.0 - sim, 6)
        drifts.append(drift)
        per_canary.append({
            "index": i,
            "string": text if len(text) <= 60 else text[:57] + "...",
            "cosine": round(sim, 6),
            "drift": drift,
        })

    if not drifts:
        return {
            "has_canary": True,
            "alarm": False,
            "error": "No comparable vectors -- dimension or count mismatch?",
            "captured_provider": snapshot.get("provider"),
            "captured_model": snapshot.get("model"),
            "provider": cur_provider,
            "model": cur_model,
        }

    max_drift = max(drifts)
    mean_drift = sum(drifts) / len(drifts)
    alarm = max_drift > threshold

    out = {
        "has_canary": True,
        "alarm": alarm,
        "threshold": threshold,
        "max_drift": round(max_drift, 6),
        "mean_drift": round(mean_drift, 6),
        "n_canaries": len(drifts),
        "captured_provider": snapshot.get("provider"),
        "captured_model": snapshot.get("model"),
        "captured_at": snapshot.get("captured_at"),
        "captured_dim": snapshot.get("dim"),
        "provider": cur_provider,
        "model": cur_model,
        "current_dim": len(cur_vectors[0]) if cur_vectors and cur_vectors[0] else None,
        "per_canary": per_canary,
    }
    if alarm:
        out["hint"] = (
            "Embedding output shifted beyond the drift threshold. The provider "
            "model likely changed; re-run embed_dataset to refresh stored "
            "vectors, then check_embedding_drift(force=true) to re-pin."
        )
    return out
