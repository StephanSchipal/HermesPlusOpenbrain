# app/config.py
import os

OPENBRAIN_MCP_URL = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8080/mcp")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SUBJECT_LINE_MODEL = os.environ.get("SUBJECT_LINE_MODEL", "claude-haiku-4-5-20251001")
GUI_DB_PATH = os.environ.get("GUI_DB_PATH", "gui.db")
DEFAULT_SEARCH_K = 25
