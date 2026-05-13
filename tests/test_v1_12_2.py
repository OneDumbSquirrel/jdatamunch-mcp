"""jdatamunch_guide tool (v1.12.2) — sibling-parity with jcm's jcodemunch_guide."""

from __future__ import annotations

import json

import pytest

from jdatamunch_mcp.server import call_tool, list_tools


class TestJdatamunchGuide:
    @pytest.mark.asyncio
    async def test_tool_registered(self):
        tools = await list_tools()
        names = {t.name for t in tools}
        assert "jdatamunch_guide" in names

    @pytest.mark.asyncio
    async def test_empty_input_schema(self):
        tools = await list_tools()
        guide = next(t for t in tools if t.name == "jdatamunch_guide")
        assert guide.inputSchema["type"] == "object"
        assert "required" not in guide.inputSchema or not guide.inputSchema["required"]

    @pytest.mark.asyncio
    async def test_returns_version_and_content(self):
        result = await call_tool("jdatamunch_guide", {})
        assert len(result) == 1
        payload = json.loads(result[0].text)
        assert "version" in payload
        assert "content" in payload
        assert isinstance(payload["content"], str)
        assert len(payload["content"]) > 0

    @pytest.mark.asyncio
    async def test_content_mentions_quickstart_tools(self):
        result = await call_tool("jdatamunch_guide", {})
        content = json.loads(result[0].text)["content"]
        for tool in ("list_datasets", "index_local", "describe_dataset",
                     "describe_column", "run_sql"):
            assert tool in content, f"quick-start tool {tool} missing from guide"

    @pytest.mark.asyncio
    async def test_content_includes_self_reference(self):
        result = await call_tool("jdatamunch_guide", {})
        content = json.loads(result[0].text)["content"]
        assert "jdatamunch_guide" in content

    @pytest.mark.asyncio
    async def test_idempotent(self):
        a = await call_tool("jdatamunch_guide", {})
        b = await call_tool("jdatamunch_guide", {})
        assert a[0].text == b[0].text
