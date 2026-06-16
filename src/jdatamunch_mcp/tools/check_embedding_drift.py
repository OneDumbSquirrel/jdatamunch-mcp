"""check_embedding_drift -- detect silent embedding-provider drift via a canary."""

from __future__ import annotations

import time
from typing import Optional

from ..embed_drift import capture_canary, check_drift


def check_embedding_drift(
    force: bool = False,
    threshold: float = 0.05,
    storage_path: Optional[str] = None,
) -> dict:
    """Detect whether the embedding provider has drifted since it was pinned.

    Column embeddings power semantic `search_data` and `find_similar_columns`.
    If the provider's model changes underneath a stored index (a model revision
    bump, a reweight under the same name, a swapped local model), the vectors
    saved at index time stop matching what the live encoder produces and
    semantic ranking quietly degrades. This tool catches that.

    It pins a small canary -- 16 deterministic strings embedded with the active
    provider, stored in `<index_path>/embed_canary.json` -- and recomputes them
    on demand, reporting the cosine drift.

    Args:
        force:     Re-embed and re-pin the canary baseline (call this once to
                   establish the baseline, and again after you intentionally
                   change providers).
        threshold: Cosine-distance alarm threshold (default 0.05). `alarm` is
                   true when the worst canary drifts past it.
        storage_path: Optional override for the index storage root.

    With `force=false` and no canary pinned yet, returns `{has_canary: false}`
    with a hint to run `force=true` first. Sibling of jcodemunch-mcp /
    jdocmunch-mcp `check_embedding_drift`.
    """
    t0 = time.perf_counter()
    if force:
        out = capture_canary(storage_path, force=True)
    else:
        out = check_drift(storage_path, threshold=threshold)
    out.setdefault("_meta", {})["timing_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    return out
