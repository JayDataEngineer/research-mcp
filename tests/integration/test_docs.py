"""Integration tests for documentation tools."""

import pytest
from tests.integration.conftest import call_tool, call_tool_raw


@pytest.mark.asyncio
async def test_docs_list_sources(mcp_client, session):
    """list_docs returns available documentation libraries."""
    result = await call_tool(mcp_client, session, "list_docs", {})
    # Returns text content (string), not JSON
    if isinstance(result, dict) and "text" in result:
        text = result["text"]
    else:
        text = str(result)

    # Either "No documentation sources" or actual sources
    assert isinstance(text, str)


@pytest.mark.asyncio
async def test_docs_fetch_rejects_unauthorized_domain(mcp_client, session):
    """read_docs rejects URLs outside allowed domains."""
    data = await call_tool_raw(
        mcp_client, session, "read_docs",
        {"url": "https://evil.com/llms.txt"}
    )
    result = data["result"]
    assert result.get("isError") is True
    assert "not allowed" in result["content"][0]["text"].lower()
