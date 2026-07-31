# app/config.py
import os

OPENBRAIN_MCP_URL = os.environ.get("OPENBRAIN_MCP_URL", "http://openbrain-mcp:8080/mcp")
OPENBRAIN_TOKEN = os.environ.get("OPENBRAIN_TOKEN", "")
GUI_DB_PATH = os.environ.get("GUI_DB_PATH", "gui.db")
DEFAULT_SEARCH_K = 25
DEFAULT_DELETE_LOG_LIMIT = 50
GRAPH_MAX_CAPTURES = 100_000
HERMES_DATA_DIR = os.environ.get("HERMES_DATA_DIR", "/hermes-data")
LEDGER_POLL_SECONDS = int(os.environ.get("LEDGER_POLL_SECONDS", "300"))
# frankfurter.app 301-redirects here. httpx does not follow redirects by
# default and raise_for_status() treats a 3xx as an error, so the old host
# broke every refresh with "Redirect response '301 Moved Permanently'".
FRANKFURTER_URL = os.environ.get(
    "FRANKFURTER_URL", "https://api.frankfurter.dev/v1/latest?from=USD&to=EUR"
)
FX_TIMEOUT_SECONDS = 10.0
