"""get_rows tool: Filtered row retrieval from a dataset."""

import json
import time
from typing import Optional

from ..config import get_index_path, MAX_COLUMNS_ROWS
from ..security import validate_filter
from ..storage.data_store import DataStore
from ..storage.sqlite_store import query_rows, MAX_ROWS_RETURNED
from ..storage.token_tracker import estimate_savings, record_savings, cost_avoided


def get_rows(
    dataset: str,
    filters: Optional[list] = None,
    columns: Optional[list] = None,
    order_by: Optional[str] = None,
    order_dir: str = "asc",
    limit: int = 50,
    offset: int = 0,
    storage_path: Optional[str] = None,
) -> dict:
    """Return filtered rows from the dataset via parameterized SQL queries.

    All column names are validated against the schema; values are SQL parameters
    (no injection surface).
    """
    t0 = time.time()

    if limit == 0 or limit < 0:
        return {"error": "ROW_LIMIT_EXCEEDED: limit must be a positive integer"}
    if limit > MAX_ROWS_RETURNED:
        return {
            "error": f"ROW_LIMIT_EXCEEDED: max rows per call is {MAX_ROWS_RETURNED}, got {limit}"
        }

    store = DataStore(base_path=storage_path or str(get_index_path()))
    idx = store.load(dataset)
    if idx is None:
        return {"error": f"NOT_INDEXED: dataset {dataset!r} is not indexed."}

    sqlite_path = store.sqlite_path(dataset)
    if not sqlite_path.exists():
        return {"error": f"NOT_INDEXED: SQLite database missing for {dataset!r}. Re-index."}

    schema_cols = idx.columns  # list of dicts

    # Auto-limit columns on wide tables when no explicit projection
    column_truncation = None
    if not columns and len(schema_cols) > MAX_COLUMNS_ROWS:
        omitted = [c["name"] for c in schema_cols[MAX_COLUMNS_ROWS:]]
        column_truncation = {
            "total_columns": len(schema_cols),
            "returned": MAX_COLUMNS_ROWS,
            "omitted_columns": omitted,
            "hint": "Use columns=['col1','col2'] to select specific columns",
        }
        columns = [c["name"] for c in schema_cols[:MAX_COLUMNS_ROWS]]

    # Validate columns projection
    if columns:
        schema_names = {c["name"] for c in schema_cols}
        bad = [c for c in columns if c not in schema_names]
        if bad:
            return {"error": f"INVALID_COLUMN: {bad}"}

    # Validate filters
    if filters:
        for f in filters:
            try:
                validate_filter(f, schema_cols)
            except ValueError as e:
                return {"error": str(e)}

    # Validate order_by
    if order_by:
        schema_names_list = {c["name"] for c in schema_cols}
        if order_by not in schema_names_list:
            return {"error": f"INVALID_COLUMN: order_by column {order_by!r} not found"}

    try:
        query_result = query_rows(
            sqlite_path=sqlite_path,
            schema_columns=schema_cols,
            filters=filters,
            columns=columns,
            order_by=order_by,
            order_dir=order_dir,
            limit=limit,
            offset=offset,
        )
    except ValueError as e:
        return {"error": str(e)}

    response_bytes = len(json.dumps(query_result["rows"]).encode("utf-8"))
    tokens_saved = estimate_savings(idx.source_size_bytes, response_bytes)
    total_saved = record_savings(tokens_saved, str(store.base_path), tool="get_rows")

    meta: dict = {
        "timing_ms": round((time.time() - t0) * 1000, 1),
        "tokens_saved": tokens_saved,
        "total_tokens_saved": total_saved,
        **cost_avoided(tokens_saved, total_saved),
    }
    if column_truncation:
        meta["column_truncation"] = column_truncation

    return {
        "result": query_result,
        "_meta": meta,
    }
