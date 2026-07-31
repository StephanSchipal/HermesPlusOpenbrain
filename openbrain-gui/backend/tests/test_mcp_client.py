# tests/test_mcp_client.py
import pytest
from mcp import types
from app.mcp_client import parse_dict_result, parse_list_result, OpenBrainMCPError

def _text_result(text: str, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=text)],
        isError=is_error,
    )

def test_parse_dict_result_decodes_single_text_block():
    result = _text_result('{"total": 3, "by_source": {}}')
    assert parse_dict_result(result) == {"total": 3, "by_source": {}}

def test_parse_dict_result_raises_on_error():
    result = _text_result("boom", is_error=True)
    with pytest.raises(OpenBrainMCPError, match="boom"):
        parse_dict_result(result)

def test_parse_list_result_reads_structured_content():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="ignored for list results")],
        structuredContent={"result": [{"keyword": "ai", "count": 3}]},
    )
    assert parse_list_result(result) == [{"keyword": "ai", "count": 3}]

def test_parse_list_result_raises_on_error():
    result = types.CallToolResult(
        content=[types.TextContent(type="text", text="nope")], isError=True,
    )
    with pytest.raises(OpenBrainMCPError, match="nope"):
        parse_list_result(result)


def test_installed_mcp_sdk_is_the_1_x_line():
    """`app/mcp_client.py` requires the mcp 1.x CLIENT api, and the pin in
    pyproject.toml is deliberate -- not laziness about staying current.

    mcp 2.0.0 changed `streamable_http_client` to yield a 2-tuple where 1.x
    yields `(read, write, get_session_id)`, and dropped `CallToolResult.isError`.
    `call_tool` unpacks three values, so a 2.x SDK dies at the context-manager
    unpack with `ValueError: not enough values to unpack (expected 3, got 2)`,
    which reaches the user as a 502 "openbrain-mcp unreachable: unhandled
    errors in a TaskGroup" -- the ExceptionGroup wrapper hides the real cause.

    That is not hypothetical: it took the live GUI's capture browsing down on
    2026-07-31, when adding an unrelated dependency invalidated the image's pip
    layer and `mcp>=1.2.0` silently re-resolved to 2.0.0. The parsing helpers
    in this module are likewise verified against 1.x internals (see
    mcp_client's own docstring). Moving to 2.x is a deliberate port, not
    something an unrelated rebuild should do behind our backs.

    Every other test here mocks `call_tool` at the module boundary, so nothing
    else in the suite exercises the real SDK surface.
    """
    import importlib.metadata as metadata

    version = metadata.version("mcp")
    assert version.split(".")[0] == "1", (
        f"mcp {version} is installed, but app/mcp_client.py targets the 1.x "
        "client API. Pin mcp<2 in pyproject.toml, or port call_tool's "
        "streamable_http_client unpack and re-verify the structuredContent "
        "semantics documented in mcp_client.py before relaxing this."
    )
