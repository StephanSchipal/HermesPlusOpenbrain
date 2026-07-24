# app/mcp_client.py
"""Thin MCP client wrapper around openbrain-mcp's Streamable HTTP endpoint.

Opens a fresh connection + session per call rather than holding a
persistent session across requests -- simplest option at this project's
personal, low-concurrency scale, and avoids reconnection/session-expiry
logic entirely.

Why two result-parsing helpers (`parse_dict_result` / `parse_list_result`):
verified by reading `mcp.server.fastmcp.utilities.func_metadata`'s
`_try_create_model_and_schema` against the installed `mcp==1.27.0` -- a bare,
un-parameterized `dict` return annotation does NOT produce `structuredContent`
on a `CallToolResult`, only `list[...]`/`dict[str, X]`/`Union` annotations do.
Concretely: `stats`, `delete`, and `update` (bare `dict`) must be parsed from
the single unstructured text block (`parse_dict_result`); `search` and
`list_keywords` (`list[dict]`) get real `structuredContent`, wrapped as
`{"result": [...]}` (`parse_list_result`). If a future `mcp` upgrade changes
this behavior, re-check that trace before collapsing the two helpers.
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
    # timeout/follow_redirects intentionally mirror create_mcp_http_client's own
    # defaults (mcp.shared._httpx_utils) -- a longer read timeout matters because
    # some tool calls (cluster_captures, classify_captures, find_near_duplicates
    # over a larger corpus) legitimately run long. Kept in sync manually since we
    # don't import that private helper; re-check on mcp version bumps.
    async with httpx.AsyncClient(
        headers=headers,
        timeout=httpx.Timeout(30.0, read=300.0),
        follow_redirects=True,
    ) as http_client:
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
    single unstructured text block instead (see module docstring for why)."""
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
