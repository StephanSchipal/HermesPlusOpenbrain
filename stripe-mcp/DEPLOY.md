# Deploying stripe-mcp to the Hermes-Agent VPS

Target: `srv1608402.hstgr.cloud`, container `hermes-agent-7qpk-hermes-agent-1`,
`default` profile only. Mirrors the `openbrain-mcp` deployment
(`README.md` Phase 4–5). **Nothing here is run until Stephan says go.**

---

## 0. Prerequisite you do in the Stripe dashboard

Create a **Restricted API key** (Developers → API keys → Create restricted key):

| Resource | Permission |
|---|---|
| Customers | **Read** |
| Charges | **Read** |
| Disputes | **Read** |
| Subscriptions | **Read** |
| Invoices | **Read** |
| Balance | **Read** |
| Products / Prices / Plans | Read (for `revenue_analytics`) |
| **Everything else** | **None** |

Make one in **test mode** (`rk_test_…`) now; make the **live** one (`rk_live_…`)
only after step 4 passes. Never paste either key into chat — put it straight
into `deploy/.env` on the VPS.

Generate the bearer token: `openssl rand -hex 32` → `STRIPE_MCP_TOKEN`.

---

## 1. Local verification (laptop)

```bash
cd stripe-mcp
pip install -e ".[dev]"
pytest -q
# then, against real test data:
STRIPE_API_KEY=rk_test_... STRIPE_MODE=test STRIPE_MCP_TOKEN=dev python -m app.server &
curl -s localhost:8080/health
curl -s -H "Authorization: Bearer dev" -X POST localhost:8080/mcp \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head
```

Expect: health `read_only:true`; `tools/list` returns the 10 read tools, no
write verbs.

## 2. Ship the code to the VPS

```bash
ssh root@srv1608402.hstgr.cloud
cd /path/to/HermesPlusOpenbrain   # same checkout the openbrain stack deploys from
git pull
# add to the SAME deploy/.env the openbrain stack uses:
#   STRIPE_API_KEY=rk_test_...
#   STRIPE_MODE=test
#   STRIPE_MCP_TOKEN=<hex from openssl>
#   STRIPE_DEFAULT_CURRENCY=EUR
$EDITOR deploy/.env
```

## 3. Bring up the container (test mode)

```bash
cd deploy
docker compose -f docker-compose.stripe.yml up -d --build
docker compose -f docker-compose.stripe.yml ps          # healthy?
docker exec hermes-agent-7qpk-hermes-agent-1 \
  python -c "import urllib.request;print(urllib.request.urlopen('http://stripe-mcp:8080/health').read())"
```

The `hermes_net` / `hermes-agent-7qpk_default` external network is already
created by the Hermes stack (same as openbrain). If compose complains it's
missing, the Hermes stack isn't up.

## 4. Register with Hermes (`default` profile, in-container)

`hermes mcp add`'s `--header` flag has a history of not persisting on this
setup (README Phase 6). **First check the syntax the installed version wants:**

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp add --help
```

Preferred (HTTP transport, bearer header):

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 \
  hermes -p default mcp add stripe \
    --transport http \
    --url http://stripe-mcp:8080/mcp \
    --header "Authorization: Bearer <STRIPE_MCP_TOKEN>"
```

If the header doesn't stick, use the interactive configurator (what actually
worked for openbrain):

```bash
ssh root@srv1608402.hstgr.cloud -t \
  'docker exec -it hermes-agent-7qpk-hermes-agent-1 hermes -p default mcp configure'
```

Verify:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes -p default mcp list      # stripe ✓ enabled
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test stripe          # Connected, 10 tools
docker exec hermes-agent-7qpk-hermes-agent-1 \
  hermes -p default chat -q "using the stripe tools, what's my current balance and MRR?"
docker exec hermes-agent-7qpk-hermes-agent-1 \
  hermes -p default chat -q "list my most recent stripe charges"
```

Confirm `openbrain` profile did **not** get it:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes -p openbrain mcp list     # no stripe
```

## 5. Go live

```bash
$EDITOR deploy/.env      # STRIPE_API_KEY=rk_live_...  ;  STRIPE_MODE=live
cd deploy && docker compose -f docker-compose.stripe.yml up -d --force-recreate
docker exec hermes-agent-7qpk-hermes-agent-1 \
  python -c "import urllib.request;print(urllib.request.urlopen('http://stripe-mcp:8080/health').read())"   # mode=live, mock=false
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test stripe
docker exec hermes-agent-7qpk-hermes-agent-1 \
  hermes -p default chat -q "what's my real stripe balance right now?"
```

## 6. Survives updates?

The openbrain MCP registration did **not** survive a Hermes container restart
via host `config.yaml` edits — it had to be set through `hermes mcp` tooling
(README Phase 5). Because step 4 uses `hermes mcp` in-container (writing
Hermes's own source of truth), it should persist like openbrain's did. **Re-run
the step 4 verify block after the next Hermes image update** (see
`project-hermes-agent-voice-update` memory for that procedure) and after one
`docker compose ... up --force-recreate hermes-agent`.

## Rollback

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes -p default mcp remove stripe
cd deploy && docker compose -f docker-compose.stripe.yml down
# revoke the restricted key in the Stripe dashboard
```

Nothing else in the Hermes stack references `stripe-mcp`; removing it is clean.
