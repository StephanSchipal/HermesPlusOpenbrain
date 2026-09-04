import os

# --- Bearer token: this server's actual security boundary --------------------
# The HTTP/MCP endpoint is otherwise unauthenticated and reachable by every
# container on the hermes-agent Docker network. Every request except /health
# must carry `Authorization: Bearer <STRIPE_MCP_TOKEN>` (see server.py).
STRIPE_MCP_TOKEN = os.environ.get("STRIPE_MCP_TOKEN", "")

# --- Stripe credentials -----------------------------------------------------
# Supply a RESTRICTED key (rk_test_... / rk_live_...) scoped to READ permissions
# only. This code registers no write tools, but the key is the boundary that
# actually holds if that ever regresses. See DEPLOY.md for the exact scopes.
STRIPE_API_KEY = os.environ.get("STRIPE_API_KEY", "rk_test_mock_key_for_development")

# "test" | "live" -- informational (drives the startup connectivity probe and
# the /health payload). The real test/live split is which key you pass above.
STRIPE_MODE = os.environ.get("STRIPE_MODE", "test").lower()

# Belt-and-braces. No write tool is registered, so this only matters if someone
# later adds one without gating it. Left here so the intent is explicit.
STRIPE_READ_ONLY = os.environ.get("STRIPE_READ_ONLY", "true").lower() in ("1", "true", "yes")

DEFAULT_CURRENCY = os.environ.get("STRIPE_DEFAULT_CURRENCY", "EUR").upper()


def is_mock_mode() -> bool:
    """True when no real Stripe key is configured -- tools return sample data so
    `hermes mcp test stripe` and local smoke tests work with no account."""
    key = STRIPE_API_KEY.lower()
    return "mock" in key or key == "rk_test_mock_key_for_development" or not key
