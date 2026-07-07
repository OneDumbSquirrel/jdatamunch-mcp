"""v1.17.0 — MCP readOnlyHint annotations (suite parity with jcodemunch PR #361).

Every tool advertises ToolAnnotations(readOnlyHint=...) so MCP clients that gate
execution (Claude Code plan mode) run jData's query tools silently while still
prompting on the handful that index / mutate / delete a dataset.
"""

from __future__ import annotations

import asyncio

from jdatamunch_mcp import server


def _tools():
    return asyncio.run(server.list_tools())


def test_every_tool_has_a_readonly_hint():
    tools = _tools()
    assert tools, "expected a non-empty tool list"
    missing = [
        t.name for t in tools
        if t.annotations is None or t.annotations.readOnlyHint is None
    ]
    assert not missing, f"tools missing readOnlyHint: {missing}"


def test_readonly_hint_matches_write_set():
    for tool in _tools():
        expected = tool.name not in server._NON_READONLY_TOOLS
        assert tool.annotations.readOnlyHint is expected, (
            f"{tool.name}: readOnlyHint={tool.annotations.readOnlyHint}, "
            f"expected {expected}"
        )


def test_write_set_names_are_real_tools():
    """Guard against a stale write-set entry that silently never applies."""
    names = {t.name for t in _tools()}
    unknown = server._NON_READONLY_TOOLS - names
    assert not unknown, f"write-set names not in the tool list: {unknown}"


def test_representative_read_and_write_tools():
    by_name = {t.name: t for t in _tools()}
    for read_tool in ("describe_dataset", "search_data", "run_sql", "get_rows", "aggregate"):
        assert by_name[read_tool].annotations.readOnlyHint is True, f"{read_tool} should be read-only"
    for write_tool in ("index_local", "delete_dataset", "embed_dataset", "ingest_sql_log"):
        assert by_name[write_tool].annotations.readOnlyHint is False, f"{write_tool} should be mutating"
