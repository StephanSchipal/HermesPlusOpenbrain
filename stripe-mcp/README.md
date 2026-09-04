# stripe-mcp (read-only, vendored)

A **read-only** Stripe MCP server for the Hermes-Agent `default` profile on the
Hostinger VPS. Lets Hermes answer questions about Stripe data (customers,
charges, subscriptions, disputes, balance, MRR, Austrian VAT) — it **cannot**
move money, issue refunds, cancel subscriptions, create customers, or generate
checkout/payment links.

## Provenance

Vendored and adapted from **[github.com/sandraschi/stripe-mcp](https://github.com/sandraschi/stripe-mcp)**
(Sandra Schipal, MIT). Kept: `austria_tax.py` (verbatim), the enums, and the
read branches of the tool handlers. Changed / added here:

| Area | Upstream | Here |
|---|---|---|
| Transport | `Mount("/", app=mcp._mcp_server)` (untested; not a valid ASGI mount) | `mcp.streamable_http_app()` served at `/mcp` — same as `openbrain-mcp` |
| MCP lib | `fastmcp>=3.4.4` (standalone) | `mcp>=1.2.0,<2` (official SDK) — matches the rest of this VPS |
| Bind address | `uvicorn ... host="127.0.0.1"` (unreachable from other containers) | `host="0.0.0.0"` |
| Auth | none on the MCP endpoint | `Authorization: Bearer <STRIPE_MCP_TOKEN>` middleware |
| Write tools | refund, cancel, customer create, checkout/invoice/payment-link | **not ported** — `checkout.py` and every write branch dropped |
| `Starlette(debug=...)` | `True` | not used (SDK app) |
| webapp / Tauri / PyInstaller | included | dropped |

Why not `STRIPE_READ_ONLY=true` on the upstream server instead: that flag only
gates `issue_refund` and subscription `cancel` upstream — customer create and
all of `checkout.py` run regardless. Dropping the tools is the honest fix.

## Tools

All read-only:

`list_customers` · `get_customer` · `list_charges` · `get_charge` ·
`list_disputes` · `list_subscriptions` · `get_subscription` · `get_balance` ·
`revenue_analytics` · `calculate_austrian_vat` (pure, no API call)

## Safety model (three layers)

1. **Code** — only read tools are registered. `test_registered_tools_are_read_only` fails CI if a write-looking tool name appears.
2. **App flag** — `STRIPE_READ_ONLY=true` (belt; only matters if someone adds an ungated write tool later).
3. **The real boundary — a Stripe *restricted* key with READ scopes only.** Even if 1–2 regress, Stripe rejects the write. Scope list in [`DEPLOY.md`](DEPLOY.md).

Plus: bearer token on the HTTP endpoint (it sits on the shared Hermes Docker
network, otherwise unauthenticated), and no public Traefik exposure.

## Local dev

```bash
cd stripe-mcp
python -m venv .venv && . .venv/Scripts/activate   # or .venv/bin/activate
pip install -e ".[dev]"
pytest -q                       # runs in mock mode, no Stripe account needed

# run it (mock mode):
STRIPE_MCP_TOKEN=dev python -m app.server
curl localhost:8080/health

# run against real Stripe test data:
STRIPE_API_KEY=rk_test_... STRIPE_MODE=test STRIPE_MCP_TOKEN=dev python -m app.server
```

## Deployment

See [`DEPLOY.md`](DEPLOY.md). Compose service: [`../deploy/docker-compose.stripe.yml`](../deploy/docker-compose.stripe.yml).
