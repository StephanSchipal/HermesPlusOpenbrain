# Deploying stripe-mcp to the Hermes-Agent VPS

**Status: DONE & LIVE since 2026-09-04.** Running on
`srv1608402.hstgr.cloud`, `default` profile only. This file is now the
redeploy / rollback / post-update runbook — steps below record what was
actually done, not a fresh-install script.

- Container `stripe-stripe-mcp-1`, image `stripe-stripe-mcp`, compose project
  **`stripe`**, file `deploy/docker-compose.stripe.yml`.
- On network `hermes-agent-7qpk_default`, reachable as `stripe-mcp:8080`. No
  Traefik, no public exposure.
- Live `rk_live_…` restricted key (Read scopes), `STRIPE_MODE=live`, account
  `acct_1Ty7rvJNkij86mLT`.
- Registered on `default` via a direct edit of the root
  `$HERMES_HOME/config.yaml` (see §4). Host backup:
  `config.yaml.pre-stripe-20260904`.

---

## 0. Stripe dashboard prerequisite

**Restricted API key** (Developers → API keys → Create restricted key), scopes:

| Resource | Permission |
|---|---|
| Customers, Charges, Disputes, Subscriptions, Invoices, Balance | **Read** |
| Products / Prices / Plans | Read (for `revenue_analytics`) |
| **Everything else** | **None** |

Never paste the key into chat — put it straight into `deploy/.env` on the VPS.
Bearer token: `openssl rand -hex 32` → `STRIPE_MCP_TOKEN`.

> **Gotcha (hit on first deploy):** `.env` ended up with the literal string
> `STRIPE_MCP_TOKEN=<openssl rand -hex 32>` instead of a generated value.
> `hermes mcp test` still passed because the container and Hermes read the same
> bogus string from the same file. Generate a real value; it must be identical
> in **`deploy/.env`** *and* in `config.yaml`'s `Authorization: Bearer …` line.

## 1. Local verification (laptop)

```bash
cd stripe-mcp
pip install -e ".[dev]"
pytest -q                       # 9 passed, mock mode, no Stripe account needed
STRIPE_MCP_TOKEN=dev python -m app.server &
curl -s localhost:8080/health   # -> {"ok":true,...,"read_only":true}
```

(A bare `tools/list` curl returns HTTP 400 — the MCP streamable-HTTP protocol
needs an `initialize` handshake first. `pytest`'s
`test_registered_tools_are_read_only` checks the tool set properly; the real
end-to-end check is `hermes mcp test stripe` in §4.)

## 2. Ship code + env to the VPS

```bash
ssh root@srv1608402.hstgr.cloud
cd /root/HermesPlusOpenbrain
git pull --ff-only
# add to the SAME deploy/.env the openbrain stack uses:
#   STRIPE_API_KEY=rk_live_...        (restricted, Read scopes)
#   STRIPE_MODE=live
#   STRIPE_MCP_TOKEN=<real openssl rand -hex 32>
#   STRIPE_DEFAULT_CURRENCY=EUR
$EDITOR deploy/.env
```

## 3. Bring up the container

```bash
cd /root/HermesPlusOpenbrain/deploy
docker compose -f docker-compose.stripe.yml up -d --build
docker compose -f docker-compose.stripe.yml ps                 # project: stripe
docker exec hermes-agent-7qpk-hermes-agent-1 \
  python -c "import urllib.request;print(urllib.request.urlopen('http://stripe-mcp:8080/health').read())"
# -> {"ok":true,"service":"stripe-mcp","mode":"live","mock":false,"read_only":true}
docker logs stripe-stripe-mcp-1 2>&1 | grep -i "connectivity ok"   # confirms the key reaches Stripe
```

The compose file sets `name: stripe`, keeping it isolated from the
`deploy`-project openbrain stack (both rooted in `deploy/`). The
`hermes-agent-7qpk_default` external network is created by the Hermes stack.

## 4. Register with Hermes — `default` profile only

`hermes mcp add … --auth header` (this Hermes version, v0.20.5) has **no flag
to pass the header value** — it wants a TTY prompt. So registration is a direct
edit of the root config, which is what `hermes mcp configure` writes anyway and
is the same shape `openbrain`'s entry has (which survives recreates).

Root config = `$HERMES_HOME/config.yaml` (`/opt/data/config.yaml`, persistent;
host path `/docker/hermes-agent-7qpk/data/config.yaml`). **`default` == this
file; every other profile (`master`, `coder`, `designer`, `researcher`,
`writer`, `openbrain`) has its own `mcp_servers:` block and does not inherit.**

Add under `mcp_servers:` (token = the real `STRIPE_MCP_TOKEN` from `deploy/.env`):

```yaml
mcp_servers:
  stripe:
    url: http://stripe-mcp:8080/mcp
    headers:
      Authorization: Bearer <STRIPE_MCP_TOKEN>
  openbrain:
    ...
```

Back up first: `cp config.yaml config.yaml.pre-stripe-$(date +%Y%m%d)`.
Then pick the gateway up:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 /command/s6-svc -r /run/service/gateway-default
```

### Verify

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes -p default mcp list      # stripe ✓ enabled
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test stripe          # ✓ Connected, 10 tools
docker exec hermes-agent-7qpk-hermes-agent-1 \
  hermes -p default chat -q "use the stripe tools — current balance, active subscriptions, customer count?"
docker exec hermes-agent-7qpk-hermes-agent-1 hermes -p openbrain mcp list    # NO stripe
# negative check — wrong bearer must 401:
docker exec hermes-agent-7qpk-hermes-agent-1 python3 -c \
 'import urllib.request as u
r=u.Request("http://stripe-mcp:8080/mcp",data=b"{}",headers={"Authorization":"Bearer wrong","Content-Type":"application/json"})
try: u.urlopen(r); print("NO 401 - BAD")
except u.HTTPError as e: print("status",e.code)'
```

## 5. Rotating the token / swapping the key

Both sides must change together:

```bash
NEWTOK=$(openssl rand -hex 32)
cd /root/HermesPlusOpenbrain
sed -i "s#^STRIPE_MCP_TOKEN=.*#STRIPE_MCP_TOKEN=$NEWTOK#" deploy/.env
# edit the Bearer line in /docker/hermes-agent-7qpk/data/config.yaml to match
cd deploy && docker compose -f docker-compose.stripe.yml up -d --force-recreate
docker exec hermes-agent-7qpk-hermes-agent-1 /command/s6-svc -r /run/service/gateway-default
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test stripe
```

Swapping the Stripe key: edit `STRIPE_API_KEY` in `deploy/.env`, then
`docker compose -f docker-compose.stripe.yml up -d --force-recreate`. No
config.yaml or gateway change needed.

## 6. After a Hermes image update or `--force-recreate hermes-agent`

`openbrain`'s `config.yaml` entry has survived container recreates, and
`stripe`'s is the same shape in the same persistent file, so it should persist
too — but **re-run the §4 verify block** after the next Hermes image update
(procedure: `project-hermes-agent-voice-update` memory). If the `stripe:` block
is gone from `config.yaml`, re-add it and restart `gateway-default`.

## Rollback

```bash
# remove the stripe: block from /docker/hermes-agent-7qpk/data/config.yaml
#   (or: docker exec ... hermes -p default mcp remove stripe)
docker exec hermes-agent-7qpk-hermes-agent-1 /command/s6-svc -r /run/service/gateway-default
cd /root/HermesPlusOpenbrain/deploy && docker compose -f docker-compose.stripe.yml down   # project: stripe
# revoke the restricted key in the Stripe dashboard
```

Nothing else in the Hermes stack references `stripe-mcp`; removal is clean.
