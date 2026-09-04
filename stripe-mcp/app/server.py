# app/server.py
#
# Read-only Stripe MCP server. Same shape as openbrain-mcp/app/server.py:
# official `mcp` SDK FastMCP, streamable-HTTP app served at /mcp, a bearer-token
# middleware as the real security boundary, a plain /health route, host 0.0.0.0.
#
# Vendored/adapted from github.com/sandraschi/stripe-mcp (Sandra Schipal).
# Rebuilt transport + auth; every write tool (refund, cancel, customer create,
# checkout/invoice creation) was dropped, not ported. See README.md.

import logging

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import config, stripe_read
from app.models import AnalyticsMetric, AustrianVatType

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("stripe_mcp.server")

# host="0.0.0.0" (not FastMCP's default "127.0.0.1") disables the MCP SDK's
# DNS-rebinding Host-header check, which otherwise 421s every request that
# isn't to a localhost Host -- i.e. every real call once Hermes reaches this
# as `stripe-mcp:8080`. The bearer middleware below is the security boundary.
mcp = FastMCP("stripe", host="0.0.0.0")


@mcp.tool()
def list_customers(limit: int = 20, email: str | None = None) -> dict:
    """List Stripe customers (most recent first). Optional `email` substring
    filter (exact match against Stripe in live mode). Read-only."""
    return stripe_read.list_customers(limit=limit, email=email)


@mcp.tool()
def get_customer(customer_id: str) -> dict:
    """Retrieve one Stripe customer by id (cus_...). Read-only."""
    return stripe_read.get_customer(customer_id)


@mcp.tool()
def list_charges(limit: int = 20, customer_id: str | None = None) -> dict:
    """List charges (payments), newest first, optionally filtered to one
    customer id (cus_...). Read-only."""
    return stripe_read.list_charges(limit=limit, customer_id=customer_id)


@mcp.tool()
def get_charge(charge_id: str) -> dict:
    """Retrieve one charge by id (ch_...). Read-only."""
    return stripe_read.get_charge(charge_id)


@mcp.tool()
def list_disputes(limit: int = 20) -> dict:
    """List payment disputes / chargebacks, newest first. Read-only."""
    return stripe_read.list_disputes(limit=limit)


@mcp.tool()
def list_subscriptions(limit: int = 20, customer_id: str | None = None,
                       status: str | None = None) -> dict:
    """List subscriptions, optionally filtered by customer id and/or status
    (e.g. "active", "canceled", "past_due"). Read-only."""
    return stripe_read.list_subscriptions(limit=limit, customer_id=customer_id, status=status)


@mcp.tool()
def get_subscription(subscription_id: str) -> dict:
    """Retrieve one subscription by id (sub_...). Read-only."""
    return stripe_read.get_subscription(subscription_id)


@mcp.tool()
def get_balance() -> dict:
    """Current Stripe account balance (available and pending, per currency).
    Read-only."""
    return stripe_read.get_balance()


@mcp.tool()
def revenue_analytics(metric: str = "all") -> dict:
    """Revenue KPIs: MRR/ARR, active subscriptions, churn, disputes, and an
    Austrian VAT summary. `metric` one of: mrr, churn, disputes, vat_summary,
    all. Read-only."""
    try:
        m = AnalyticsMetric(metric)
    except ValueError:
        return {"success": False, "error": f"unknown metric {metric!r}; "
                f"expected one of {[e.value for e in AnalyticsMetric]}"}
    return stripe_read.revenue_analytics(m)


@mcp.tool()
def calculate_austrian_vat(amount: float, vat_type: str = "standard_20",
                           customer_vat_id: str | None = None) -> dict:
    """Compute Austrian VAT on a net EUR amount. `vat_type` one of standard_20,
    reduced_10, reduced_13. If `customer_vat_id` is a syntactically valid ATU
    id, reverse charge (0%) is applied. Pure calculation -- does not call
    Stripe. Returns the tax breakdown plus BAO § 132 retention metadata."""
    try:
        vt = AustrianVatType(vat_type)
    except ValueError:
        return {"success": False, "error": f"unknown vat_type {vat_type!r}; "
                f"expected one of {[e.value for e in AustrianVatType]}"}
    return stripe_read.austrian_vat(amount, vt, customer_vat_id)


# --- HTTP plumbing --------------------------------------------------------
class BearerAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        expected = f"Bearer {config.STRIPE_MCP_TOKEN}"
        if not config.STRIPE_MCP_TOKEN or request.headers.get("authorization") != expected:
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)


async def _health(_request: Request) -> JSONResponse:
    return JSONResponse({
        "ok": True,
        "service": "stripe-mcp",
        "mode": config.STRIPE_MODE,
        "mock": config.is_mock_mode(),
        "read_only": True,
    })


def _probe() -> None:
    if config.is_mock_mode():
        logger.info("stripe-mcp starting in MOCK mode (no real Stripe key).")
        return
    try:
        import stripe

        stripe.api_key = config.STRIPE_API_KEY
        acct = stripe.Account.retrieve()
        logger.info("Stripe connectivity ok (account %s, mode=%s).",
                    acct.get("id", "?"), config.STRIPE_MODE)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Stripe probe failed: %s -- tools will surface errors per call.", exc)


def build_app() -> Starlette:
    app = mcp.streamable_http_app()  # Starlette app serving MCP at /mcp
    app.router.routes.append(Route("/health", _health, methods=["GET"]))
    app.add_middleware(BearerAuthMiddleware)
    return app


if __name__ == "__main__":
    import uvicorn

    _probe()
    uvicorn.run(build_app(), host="0.0.0.0", port=8080)
