# app/mcp_client.py
"""Thin MCP client wrapper around openbrain-mcp's Streamable HTTP endpoint.

Opens a fresh connection + session per call rather than holding a
persistent session across requests -- simplest option at this project's
personal, low-concurrency scale, and avoids reconnection/session-expiry
logic entirely.
"""
import json
import httpx
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client
from app.config import OPENBRAIN_MCP_URL, OPENBRAIN_TOKEN

class OpenBrainMCPError(RuntimeError):
    """Raised when openbrain-mcp returns an error result for a tool call."""

async def call_tool(name: str, arguments: dict) -> types.CallToolResult:
    # A plain httpx.AsyncClient (not mcp's own create_mcp_http_client helper,
    # which lives under a private `_httpx_utils` module) -- this is the
    # documented way to attach custom headers to streamable_http_client, per
    # its own docstring, without depending on an underscore-prefixed
    # (unstable) internal API.
    headers = {"Authorization": f"Bearer {OPENBRAIN_TOKEN}"}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(30.0)) as http_client:
        async with streamable_http_client(OPENBRAIN_MCP_URL, http_client=http_client) as (
            read, write, _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, arguments)

def _error_message(result: types.CallToolResult) -> str:
    return " ".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    ) or "unknown error"

def parse_dict_result(result: types.CallToolResult) -> dict:
    """For tools with a bare `dict` return annotation (`stats`, `delete`,
    `update`) -- no structuredContent in this mcp SDK version, so parse the
    single unstructured text block instead (see module docstring context
    in the implementation plan for why)."""
    if result.isError:
        raise OpenBrainMCPError(_error_message(result))
    block = result.content[0]
    assert isinstance(block, types.TextContent)
    return json.loads(block.text)

def parse_list_result(result: types.CallToolResult) -> list[dict]:
    """For tools with a `list[dict]` return annotation (`search`,
    `list_keywords`) -- these DO get structuredContent, wrapped as
    {"result": [...]}."""
    if result.isError:
        raise OpenBrainMCPError(_error_message(result))
    assert result.structuredContent is not None, (
        "expected structuredContent for a list-returning tool"
    )
    return result.structuredContent["result"]
