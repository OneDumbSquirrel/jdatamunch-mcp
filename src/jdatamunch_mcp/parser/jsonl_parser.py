"""Streaming JSONL/NDJSON parser."""

import json
import os
from pathlib import Path
from typing import Generator, Optional

from .normalize import normalize_native
from .types import ColumnInfo, ParsedDataset

_SCHEMA_SAMPLE_LINES = 100


def _detect_encoding(path: str) -> str:
    """Detect file encoding using charset-normalizer."""
    try:
        from charset_normalizer import from_path
        result = from_path(path, cp_isolation=["utf-8", "latin-1", "cp1252"])
        if result.best():
            return str(result.best().encoding)
    except Exception:
        pass
    return "utf-8"


def _discover_columns(path: str, encoding: str) -> list:
    """Read first N lines to build ordered column list (union of all keys)."""
    columns: dict = {}
    with open(path, encoding=encoding, errors="replace") as f:
        for i, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    for key in obj:
                        columns[key] = None
            except json.JSONDecodeError:
                continue
            if i >= _SCHEMA_SAMPLE_LINES:
                break
    return list(columns.keys())


def _row_generator(path: str, encoding: str, column_names: list) -> Generator:
    """Yield data rows as lists of strings, aligned to column_names."""
    with open(path, encoding=encoding, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(obj, dict):
                continue
            yield [
                normalize_native(obj.get(col), "jsonl")
                for col in column_names
            ]


def parse_jsonl(
    path: str,
    encoding: Optional[str] = None,
) -> ParsedDataset:
    """Parse a JSONL/NDJSON file and return a streaming ParsedDataset."""
    path = str(Path(path).resolve())
    file_size = os.path.getsize(path)

    if not encoding:
        encoding = _detect_encoding(path)

    column_names = _discover_columns(path, encoding)
    if not column_names:
        raise ValueError(f"No valid JSON objects found in {path}")

    columns = [ColumnInfo(name=name, position=i) for i, name in enumerate(column_names)]

    with open(path, encoding=encoding, errors="replace") as f:
        sample = f.read(65536)
    sample_lines = sample.count("\n")
    if sample_lines > 0:
        bytes_per_line = len(sample.encode(encoding, errors="replace")) / sample_lines
        estimated_rows = int(file_size / bytes_per_line)
    else:
        estimated_rows = 0

    metadata = {
        "encoding": encoding,
        "delimiter": None,
        "header_row": None,
        "estimated_rows": estimated_rows,
        "file_size": file_size,
    }

    return ParsedDataset(
        columns=columns,
        row_iterator=_row_generator(path, encoding, column_names),
        metadata=metadata,
    )
