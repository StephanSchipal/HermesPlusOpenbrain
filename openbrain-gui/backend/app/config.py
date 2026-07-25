# app/config.py
import os

OPENBRAIN_MCP_URL = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8080/mcp")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
GUI_DB_PATH = os.environ.get("GUI_DB_PATH", "gui.db")
DEFAULT_SEARCH_K = 25
DEFAULT_DELETE_LOG_LIMIT = 50
