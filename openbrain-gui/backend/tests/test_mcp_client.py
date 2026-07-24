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
