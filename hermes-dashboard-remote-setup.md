# Hermes Dashboard — Laptop → Hostinger VPS Setup

Step-by-step guide for using the Hermes web dashboard from a laptop while the
Hermes Agent runs in Docker on a Hostinger VPS.

Sources (checked 2026-07-03):

- [Web Dashboard docs](https://hermes-agent.nousresearch.com/docs/user-guide/features/web-dashboard)
- [Docker guide](https://hermes-agent.nousresearch.com/docs/user-guide/docker)
- [Desktop App docs](https://hermes-agent.nousresearch.com/docs/user-guide/desktop)

---

## Key architectural insight

**The dashboard is not a separate app you install on the laptop.** It is a web
server built into Hermes Agent itself, so it runs **inside the Docker container
on the VPS**. From the laptop there are two ways to use it:

| Option | What runs on the laptop | Install needed? |
| --- | --- | --- |
| A — Browser | Just a browser tab at `http://<vps-ip>:9119` | No |
| B — Hermes Desktop | Native Electron app pointed at the remote backend | Yes (installer) |

Both options require the same server-side setup first.

---

## Part 1: Server side (Hostinger VPS, Docker)

### Step 1 — Set dashboard credentials

SSH into the VPS and add login credentials to the Hermes data directory
(`~/.hermes` on the host, mounted into the container at `/opt/data`):

```bash
cat >> ~/.hermes/.env <<'EOF'
HERMES_DASHBOARD_BASIC_AUTH_USERNAME=admin
HERMES_DASHBOARD_BASIC_AUTH_PASSWORD=choose-a-strong-password
EOF
echo "HERMES_DASHBOARD_BASIC_AUTH_SECRET=$(openssl rand -base64 32)" >> ~/.hermes/.env
chmod 600 ~/.hermes/.env
```

Notes:

- `HERMES_DASHBOARD_BASIC_AUTH_SECRET` keeps sessions valid across container
  restarts (without it you are signed out on every restart).
- Without a configured auth provider, a publicly-bound dashboard **fails
  closed at startup** — it refuses to serve. The old
  `HERMES_DASHBOARD_INSECURE=1` bypass is a deprecated no-op since the
  June 2026 hardening.
- Prefer no plaintext password at rest? Use
  `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH` with an scrypt hash instead.

### Step 2 — Run the container with the dashboard enabled

The official image runs the dashboard as a supervised s6-rc service alongside
the gateway when `HERMES_DASHBOARD=1`:

```bash
docker stop hermes && docker rm hermes

docker run -d \
  --name hermes \
  --restart unless-stopped \
  -v ~/.hermes:/opt/data \
  -p 8642:8642 \
  -p 9119:9119 \
  -e HERMES_DASHBOARD=1 \
  nousresearch/hermes-agent gateway run
```

Relevant environment variables (official image):

| Variable | Description | Default |
| --- | --- | --- |
| `HERMES_DASHBOARD` | `1`/`true`/`yes` enables the supervised dashboard service | unset (service stays down) |
| `HERMES_DASHBOARD_HOST` | Bind address inside the container | `0.0.0.0` |
| `HERMES_DASHBOARD_PORT` | Dashboard HTTP port | `9119` |

Inside the container the dashboard binds `0.0.0.0` by default, so the
published `-p 9119:9119` port just works.

**Custom Dockerfile instead of the official image?** Install the web extra in
the hermes-agent checkout and run the dashboard as the service process:

```bash
uv pip install -e ".[web,pty]"
hermes dashboard --no-open --host 0.0.0.0 --port 9119
```

### Step 3 — Open the firewall port

In Hostinger hPanel: **VPS → your server → Firewall** → allow TCP 9119 —
ideally restricted to your home IP only (see security section below).

### Step 4 — Verify the auth gate

```bash
curl -s http://<vps-ip>:9119/api/status | jq '.auth_required, .auth_providers'
# expected:
# true
# ["basic"]
```

| Result | Meaning |
| --- | --- |
| `true` + `["basic"]` | Correct — sign-in will work |
| `false` | Bind is loopback or gate didn't engage — fix the bind address |
| `true` but no `"basic"` | Credential env vars weren't loaded — check `.env` location |

---

## Part 2: Laptop side

### Option A — Browser (nothing to install)

Open `http://<vps-ip>:9119` and sign in with the credentials from Step 1.
Full dashboard: Chat, Config, API Keys, Sessions, Logs, Analytics, Cron,
Profiles, Skills, MCP, Webhooks, Pairing, Channels, System.

### Option B — Hermes Desktop app (Windows)

1. Download the Hermes Desktop installer from the
   [Hermes website](https://hermes-agent.nousresearch.com/) (Desktop Download
   link) and run it. First launch auto-provisions Python (via uv), Node, and
   PortableGit through `install.ps1`.
2. In the app: **Settings → Gateway → Remote gateway**
3. **Remote URL**: `http://<vps-ip>:9119` (reverse-proxy path prefixes like
   `/hermes` are supported)
4. Click **Sign in** — the app detects the username/password provider and
   shows a credential form.
5. **Save and reconnect** — the desktop shell switches onto the remote
   backend.

Alternative: set `HERMES_DESKTOP_REMOTE_URL=http://<vps-ip>:9119` before
launching the app to override the in-app URL (you still sign in from the
Gateway panel).

**Gotcha:** Desktop's "backend is ready" probe only hits `GET /api/status`
(public). Live chat uses a separate WebSocket to `/api/ws` that additionally
requires authentication and a Host-header/peer-IP match — so "ready but chat
never works" usually means an auth or bind problem, not a network problem.

---

## Security — important

The docs are explicit: username/password auth is **not suitable for direct
public-internet exposure**. The dashboard reads/writes the `.env` (API keys,
secrets) and can run agent commands. A Hostinger VPS is on the public
internet, so pick one:

1. **Best: Tailscale (VPN).** Install on VPS + laptop, bind the dashboard to
   the tailscale IP (`HERMES_DASHBOARD_HOST=<tailscale-ip>`), use
   `http://<tailscale-ip>:9119` as the URL. Only tailnet devices can reach it;
   no public firewall port needed.
2. **Public exposure: Nous Portal OAuth.** The provider designed for publicly
   reachable backends. Run `hermes dashboard register` to provision the OAuth
   client (`HERMES_DASHBOARD_OAUTH_CLIENT_ID`), then "Sign in with Nous
   Research".
3. **Self-hosted OIDC.** Authenticate against your own IdP:
   `HERMES_DASHBOARD_OIDC_ISSUER` + `HERMES_DASHBOARD_OIDC_CLIENT_ID`.
4. **Absolute minimum:** restrict the hPanel firewall rule for 9119 to your
   home IP only. (Home IPs rotate — expect to update the rule.)

---

## Troubleshooting quick reference

| Symptom | Cause / fix |
| --- | --- |
| Connection refused / timeout | Port not published, firewall closed, or bind is loopback |
| Sign-in 401 "Invalid credentials" | Username/password mismatch with backend `.env` (same generic error for both) |
| No "Sign in" button, asks for session token | Basic provider not active — env vars not loaded (`/api/status` won't list `"basic"`) |
| Signed out on every restart | `HERMES_DASHBOARD_BASIC_AUTH_SECRET` missing or unstable |
| Desktop "ready" but chat fails | Check dashboard logs for `/api/ws` close code: `4403` = Host/peer-IP guard rejected, `4401` = WS ticket didn't authenticate |
| Dashboard won't start at all | Non-loopback bind with no auth provider — fails closed by design |
