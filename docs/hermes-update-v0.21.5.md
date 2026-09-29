# Hermes-Agent Update v0.20.5 → v0.21.5 — Runbook

Vorbereitet 2026-09-29. Container `hermes-agent-7qpk-hermes-agent-1`, Image
`ghcr.io/hostinger/hvps-hermes-agent:latest` (neu: `03295aaee23c`, v0.21.5 / 2026.9.24).
Rollback-Tag des bisherigen Images: `hvps-hermes-agent:pre-update-2026-09-29` (`01c0b124eda7`).
Das neue Image ist bereits gepullt.

Alle Befunde unten stammen aus einer Generalprobe: neues Image auf einer Kopie von
`/opt/data`, isoliert mit `--network none`, danach gelöscht.

## Was sich ändert und uns trifft

### 1. Nur noch EIN Gateway pro Container (Multiplex) — bricht WhatsApp

Ab v0.21 startet `cont-init.d/02-reconcile-profiles` nur noch den Root-Slot
`gateway-default`, der alle Profile bedient. Die Profil-Slots (`gateway-openbrain` usw.)
werden registriert, aber **nie** gestartet. `gateway.multiplex_profiles: false` ist als
Opt-out abgeschafft.

Der Multiplexer bedient WhatsApp **nur auf dem `default`-Profil**. Das Log dazu:
`whatsapp is enabled in profile(s) openbrain but not on the default profile — the
platform is not being served`. Upstream führt „WhatsApp bridge/relay on secondaries“
als offene Lücke. Ohne Gegenmaßnahme wäre der WhatsApp→OpenBrain-Bot nach dem Update
**still tot**.

**Gegenmaßnahme:** `gateway.standalone: true` in `profiles/openbrain/config.yaml`
(offizieller „temporary compatibility shim“). Dann bedient der Multiplexer `openbrain`
nicht mehr, und `openbrain` bekommt wieder einen eigenen Gateway mit eigener
WhatsApp-Bridge. In der Probe verifiziert: `[Whatsapp] Bridge HTTP ready`.

Der Container startet einen Standalone-Slot beim Boot aber **nicht**. Auch
`hermes -p openbrain gateway start` übersteht keinen Neustart (verifiziert). Deshalb gibt
es den neuen Host-Watchdog `scripts/hermes-standalone-gateways-watchdog.sh` (jede Minute).
Er zieht `gateway-openbrain` hoch, aber nur, wenn das Profil wirklich
`standalone: true` hat.

### 2. Alter Config-Key schaltet WhatsApp jetzt wirklich ab

`profiles/openbrain/config.yaml` enthält (Zeile ~504, unter `gateway:`):

```yaml
  whatsapp:
    enabled: false
```

v0.20.5 ignoriert das (siehe CaptureBotDocu.md: „not a real config key“). v0.21.5 wertet
es als explizites Abschalten, und `WHATSAPP_ENABLED=true` aus der `.env` überstimmt es
nicht mehr. **Muss vor dem Update raus.** Das gleiche tote Stück steht auch in
`profiles/master/config.yaml`. Dort ist es harmlos, weil master WhatsApp ohnehin aus hat.

### 3. Profil-API-Ports

Im Multiplex-Modus hängen `api_server` von coder/designer/master/researcher/writer unter
`http://127.0.0.1:8642/p/<profil>/v1` statt auf eigenen Ports (8643, …). Bei uns nutzt
niemand diese Ports (weder Traefik noch Voice noch GUI). `openbrain` behält als
Standalone-Gateway 8644.

### 4. Unkritisch, aber gut zu wissen

- Config-Schema-Migration 38 → 46 beim ersten Boot. Backups legt Hermes selbst unter
  `/opt/data/backups/config/` an.
- `state.db` (Root + Profile): Die Spalten von `sessions` gehen von 56 auf 59, rein
  additiv, alle Zeilen bleiben erhalten. Die Cost-Page der OpenBrain-GUI bleibt damit
  kompatibel.
- `hermes -p <p> acp` (Buzz-Bridges) existiert weiter.
- Cron läuft im Multiplexer weiter (`voice-server-watchdog` tickt). Die Warnung
  `Job 'ffbd2b95941e': platform 'whatsapp' not configured` betrifft nur die Zustellung
  des Outputs (`deliver=origin`), nicht das Skript.
- Dashboard: harmlose Warnungen `Plugin 'basic' tried to register dashboard-auth provider
  … from profile scope` (je Profil eine).
- Neue Standard-SOUL.md im Image. Unsere Profile haben eigene SOUL.md in `/opt/data`,
  die bleiben unberührt.

### 5. Buzz-Bridges: PID-Reuse-Bug (auch ohne Update schon aktiv)

Beim Vorbereiten gefunden: Nur 3 von 7 Buzz-Bridges liefen (default, openbrain, designer
und master fehlten). Ursache: Nach einem Container-Neustart vergibt der Kernel die PIDs
von vorn. Die gespeicherte `<p>.pid` zeigte dann auf einen **anderen** lebenden Prozess
(z.B. designer.pid = die buzz-acp von writer, default.pid = ein Gateway-Thread).
`kill -0` meldete „läuft“, also startete der Supervisor nie neu. Der Update-Recreate
würde das wieder auslösen.

**Fix:** In `scripts/buzz-agent-supervise.sh` prüft `is_running()` zusätzlich die Marke
`BUZZ_AGENT_PROFILE=<name>` in `/proc/<pid>/environ`. Das Regressions-Szenario ist in
`scripts/tests/buzz-agent-supervise.test.sh` abgedeckt (auf dem VPS-Host PASS mit sh und
bash). Ein Trockenlauf gegen die Live-PID-Dateien meldet genau die 4 fehlenden als
„würde starten“. Ausgerollt wird der Fix per `git pull` auf dem VPS, denn der
Buzz-Watchdog synct `supervise.sh` selbst aus dem Repo.

## Ablauf

```bash
ssh root@srv1608402.hstgr.cloud
C=hermes-agent-7qpk-hermes-agent-1
```

### A. Vorab (bei laufendem v0.20.5 — dort wirkungslos, also gefahrlos)

```bash
# A1. Repo-Stand mit Supervisor-Fix + neuem Watchdog holen (nach Push)
cd /root/HermesPlusOpenbrain && git pull
#     -> innerhalb 1 Min starten die 4 fehlenden Buzz-Bridges; prüfen:
docker exec $C pgrep -c -f /opt/data/bin/buzz-acp          # erwartet: 7

# A2. openbrain: stale Key raus, standalone rein (als hermes, Owner bleibt korrekt)
docker exec -u hermes $C /opt/hermes/.venv/bin/python - <<'EOF'
import re
p = "/opt/data/profiles/openbrain/config.yaml"
s = open(p).read()
n = re.sub(r"\n  whatsapp:\n    enabled: false\n", "\n", s, count=1)
assert n != s, "stale gateway.whatsapp block not found"
open(p, "w").write(n)
print("removed")
EOF
docker exec -u hermes $C hermes -p openbrain config set gateway.standalone true --force
docker exec $C grep -n -A1 -E "^gateway:|standalone" /opt/data/profiles/openbrain/config.yaml

# A3. Standalone-Watchdog in die Host-Crontab (bei v0.20.5 ein No-op, Slot läuft schon)
chmod +x /root/HermesPlusOpenbrain/scripts/hermes-standalone-gateways-watchdog.sh
(crontab -l; echo '* * * * * /root/HermesPlusOpenbrain/scripts/hermes-standalone-gateways-watchdog.sh') | crontab -
```

### B. Update

```bash
cd /docker/hermes-agent-7qpk
# B1. Konsistentes Voll-Backup von /opt/data (Container kurz gestoppt, ~3.8 GB)
docker compose stop hermes-agent
mkdir -p /root/backups/hermes && tar czf /root/backups/hermes/data-pre-0.21.5-$(date +%F).tgz -C /docker/hermes-agent-7qpk data
# B2. Recreate auf dem bereits gepullten Image
docker compose up -d --force-recreate hermes-agent
# B3. Voice nicht auf den 5-Min-Cron warten lassen
sleep 60; docker exec $C bash /opt/data/scripts/voice_watchdog.sh
```

### C. Verifikation

```bash
docker exec $C hermes --version                                   # v0.21.5
docker logs $C 2>&1 | grep reconcile                              # default=started, Rest=registered
docker exec $C ps -eo args | grep "gateway run" | grep -v grep    # 2 Stück: root + "-p openbrain"
cat /var/log/hermes-standalone-gateways-watchdog.log              # "openbrain: ... started"
docker exec $C tail -n 30 /opt/data/logs/gateways/openbrain/current | grep -i whatsapp   # Bridge ready / connected
docker exec $C hermes profile list                                # alle 7 running
docker exec $C pgrep -c -f /opt/data/bin/buzz-acp                 # 7 (innerhalb 1-2 Min)
docker inspect $C --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'
grep url /docker/traefik/dynamic/voice.yml                        # muss zur IP passen (war 172.16.1.2)
curl -s https://srv1608402.hstgr.cloud/voice/health               # {"status":"ok"}
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://srv1608402.hstgr.cloud/voice/inbound -d 'CallSid=x'   # 403
docker exec $C hermes mcp test openbrain                          # Connected, 11 tools
docker exec $C hermes mcp test stripe                             # Connected
docker exec -u hermes $C hermes -p openbrain mcp test openbrain   # Connected
```

Danach die echten End-to-End-Tests (brauchen Stephan):
- WhatsApp: Nachricht an den Bot schicken und einen Save auslösen. Der Eintrag muss in
  OpenBrain landen.
- Buzz: `@Hermes` und `@Hermes-openbrain` in `#hermes` erwähnen, beide antworten.
- Twilio: Testanruf.
- GUI: Cost-Page laden, „All“ und ein einzelner Bot.

### D. Rollback

```bash
cd /docker/hermes-agent-7qpk
docker compose stop hermes-agent
# Daten auf den Stand vor dem Update (Config-Migration + state.db-Spalten zurück)
mv data data.failed-0.21.5 && tar xzf /root/backups/hermes/data-pre-0.21.5-*.tgz -C /docker/hermes-agent-7qpk
docker tag hvps-hermes-agent:pre-update-2026-09-29 ghcr.io/hostinger/hvps-hermes-agent:latest
docker compose up -d --force-recreate hermes-agent
```

Die Vorab-Änderungen aus A2 sind unter v0.20.5 wirkungslos und können drinbleiben. Der
Watchdog aus A3 ist ebenfalls harmlos, denn bei v0.20.5 läuft der Slot ohnehin.

## Nach dem Update nachziehen (Doku)

- `CaptureBotDocu.md`, Abschnitt „How multi-profile gateways are supervised“: beschreibt
  noch das Modell „N Gateways“. Umstellen auf Multiplex + openbrain standalone + Watchdog.
- `CaptureBotDocu.md` Gotcha „WhatsApp on/off is the `.env` var“: gilt so nicht mehr. Ein
  expliziter Config-Eintrag schlägt jetzt die `.env`.
- Memory/Runbook-Prozedur um die Schritte A2/A3 ergänzen.
- `gateway.standalone` ist upstream als temporär markiert. Bei jedem künftigen Update
  prüfen, ob WhatsApp auf Sekundärprofilen im Multiplexer inzwischen geht. Dann
  `hermes gateway migrate --multiplex` statt Standalone.
