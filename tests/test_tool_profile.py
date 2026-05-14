"""Tests for JDATAMUNCH_TOOL_PROFILE / JDATAMUNCH_DISABLED_TOOLS filtering (issue #297)."""

import asyncio
import json

import pytest

from jdatamunch_mcp.server import (
    _all_tools,
    _filter_tools,
    _TOOL_TIER_CORE,
    _TOOL_TIER_STANDARD,
    _ALWAYS_PRESENT_TOOLS,
    call_tool,
    list_tools,
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("JDATAMUNCH_TOOL_PROFILE", raising=False)
    monkeypatch.delenv("JDATAMUNCH_DISABLED_TOOLS", raising=False)
    yield


def test_full_default_returns_every_tool():
    tools = _filter_tools(_all_tools())
    assert len(tools) == len(_all_tools())


def test_core_profile_drops_to_core_tier_plus_always_present(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", "core")
    tools = _filter_tools(_all_tools())
    names = {t.name for t in tools}
    assert names <= _TOOL_TIER_CORE | _ALWAYS_PRESENT_TOOLS
    assert "jdatamunch_guide" in names
    assert "run_sql" not in names


def test_standard_profile_is_superset_of_core(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", "standard")
    tools = _filter_tools(_all_tools())
    assert _TOOL_TIER_CORE <= {t.name for t in tools}


def test_invalid_profile_falls_back_to_full(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", "ultra")
    tools = _filter_tools(_all_tools())
    assert len(tools) == len(_all_tools())


def test_disabled_tools_filters_out_named_tools(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_DISABLED_TOOLS", "run_sql, plan_query")
    names = {t.name for t in _filter_tools(_all_tools())}
    assert "run_sql" not in names
    assert "plan_query" not in names
    assert "search_data" in names


def test_disabled_tools_can_hide_jdatamunch_guide(monkeypatch):
    """jdatamunch_guide is documentation, not a control surface."""
    monkeypatch.setenv("JDATAMUNCH_DISABLED_TOOLS", "jdatamunch_guide")
    names = {t.name for t in _filter_tools(_all_tools())}
    assert "jdatamunch_guide" not in names


def test_call_tool_rejects_disabled_tool(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_DISABLED_TOOLS", "run_sql")
    out = asyncio.run(call_tool("run_sql", {}))
    payload = json.loads(out[0].text)
    assert "disabled" in payload["error"].lower()


def test_list_tools_async_entrypoint_applies_filter(monkeypatch):
    monkeypatch.setenv("JDATAMUNCH_TOOL_PROFILE", "core")
    tools = asyncio.run(list_tools())
    assert all(t.name in (_TOOL_TIER_CORE | _ALWAYS_PRESENT_TOOLS) for t in tools)
