# Hermes-Agent ↔ Laptop — Dateizugriff via Tailscale + MCP

Gibt dem Hermes-Agent (läuft im Docker-Container auf der Hostinger-VPS) Lese-/
Schreibzugriff auf **genau ein** Verzeichnis auf einem Laptop — nicht mehr. Der
Laptop ist kein Server (wechselnde IP, NAT, nicht immer online), deshalb läuft
die Verbindung über ein privates Tailscale-VPN statt über eine öffentliche
Portfreigabe.

> Status: **Live** (seit 2026-07-29, zuletzt aktualisiert 2026-07-31 —
> Hidden-Wrapper gegen Fenster-Flash beim Neustart; davor 2026-07-30:
> Auto-Recovery-Watchdog + Logging)
> Freigegebenes Verzeichnis: `D:\projects\Hermes` (Laptop `gpdsteve`)
> MCP-Server-Name in Hermes: `laptop_fs`

---

## Inhalt

- [Überblick](#überblick)
- [Architektur](#architektur)
- [Setup — Tailscale](#setup--tailscale)
- [Setup — MCP-Filesystem-Server (Laptop)](#setup--mcp-filesystem-server-laptop)
- [Setup — Windows-Firewall](#setup--windows-firewall)
- [Setup — Persistenz (Scheduled Task)](#setup--persistenz-scheduled-task)
- [Setup — Hermes-Registrierung](#setup--hermes-registrierung)
- [Setup — Auto-Recovery-Watchdog (VPS)](#setup--auto-recovery-watchdog-vps)
- [Chat-Beispiele / Verifikation](#chat-beispiele--verifikation)
- [Betrieb](#betrieb)
- [Troubleshooting](#troubleshooting)
- [Sicherheit](#sicherheit)
- [Wartung & Grenzfälle](#wartung--grenzfälle)

---

## Überblick

| Aspekt | Entscheidung |
|---|---|
| **Transportweg** | Tailscale (privates, gegenseitig authentifiziertes WireGuard-Mesh) — kein öffentlich erreichbarer Port. |
| **Zugriffskontrolle (Verzeichnis)** | `@modelcontextprotocol/server-filesystem` — harte Pfad-Whitelist auf Anwendungsebene, verweigert alles außerhalb. |
| **Zugriffskontrolle (Netzwerk)** | Windows-Firewall-Regel, die Port 8931 auf das Tailscale-Subnetz (`100.64.0.0/10`) beschränkt. |
| **Transport-Protokoll** | MCP Streamable HTTP, via `supergateway` (bridged den stdio-Filesystem-Server auf HTTP). |
| **Auth-Token** | Keiner nötig — Tailscale ist bereits geräteweise authentifiziert; die Firewall-Regel ist zusätzliche Absicherung. |
| **Persistenz** | Windows Scheduled Task, startet den Server bei jeder Anmeldung neu, mit Auto-Restart. |
| **Registrierung in Hermes** | `hermes mcp add` (nicht direktes `config.yaml`-Edit — siehe [Sicherheit](#sicherheit)/Troubleshooting). |
| **Auto-Recovery** | Cronjob auf der VPS erkennt, wenn der Laptop nach einer Downtime wieder erreichbar wird, und startet den Hermes-Container automatisch neu — sonst reconnectet der Gateway-Prozess nicht von selbst (siehe [Auto-Recovery-Watchdog](#setup--auto-recovery-watchdog-vps)). |

---

## Architektur

```
Laptop (gpdsteve, 100.99.233.106)                 Hostinger-VPS (srv1608402, 100.110.206.80)
┌──────────────────────────────────┐              ┌───────────────────────────────────────┐
│ Scheduled Task                    │              │ hermes-agent-7qpk-hermes-agent-1        │
│  → supergateway (Port 8931)       │              │   (Docker-Container)                    │
│     --stdio                       │  Tailscale   │                                          │
│     "@modelcontextprotocol/       │◀────────────▶│   MCP-Client: laptop_fs                 │
│      server-filesystem            │  (WireGuard, │   http://100.99.233.106:8931/mcp        │
│      D:\projects\Hermes"          │   privat)    │                                          │
│                                    │              │                                          │
│ Windows Firewall:                 │              └───────────────────────────────────────┘
│  Port 8931 nur aus 100.64.0.0/10  │
└──────────────────────────────────┘
```

Zwei unabhängige Schutzschichten:
1. **Netzwerk-Ebene:** Ohne Tailscale-Verbindung ist Port 8931 gar nicht erreichbar (weder aus dem Internet noch aus dem Heimnetz — Firewall blockiert alles außer dem Tailscale-Subnetz).
2. **Anwendungs-Ebene:** Selbst wer den Port erreicht, bekommt vom Filesystem-Server nur Zugriff auf `D:\projects\Hermes` — jeder andere Pfad wird mit einer expliziten Fehlermeldung abgelehnt (verifiziert, siehe [Chat-Beispiele](#chat-beispiele--verifikation)).

---

## Setup — Tailscale

Beide Geräte müssen im **selben Tailnet** (gleicher Account) registriert sein.

### 1. Laptop

```powershell
tailscale up
```
Liefert einen Login-Link (`https://login.tailscale.com/a/...`) — im Browser öffnen und anmelden. Danach IP prüfen:

```powershell
tailscale ip -4
# → 100.99.233.106
```

### 2. VPS

```bash
ssh root@srv1608402.hstgr.cloud
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```
Auch hier liefert `tailscale up` einen Login-Link — **mit demselben Account** wie beim Laptop anmelden, sonst landen die Geräte in unterschiedlichen Tailnets und sehen sich nicht.

### Verbindung prüfen

```bash
# Auf dem VPS:
tailscale ping -c 2 100.99.233.106
# → pong from gpdsteve (100.99.233.106) via ... in 55-100ms
```

Der Hermes-Container erreicht die Tailscale-IPs direkt über die Docker-Bridge —
kein `--network host` oder zusätzliches Docker-Networking nötig:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 ping -c 2 100.99.233.106
```

---

## Setup — MCP-Filesystem-Server (Laptop)

Ein Prozess, zwei Bausteine: `@modelcontextprotocol/server-filesystem` (stdio,
setzt die Pfad-Whitelist durch) gebridged über `supergateway` (macht daraus
einen HTTP/Streamable-HTTP-Endpunkt):

```powershell
npx -y supergateway --stdio "npx -y @modelcontextprotocol/server-filesystem D:\projects\Hermes" --outputTransport streamableHttp --port 8931 --streamableHttpPath /mcp --logLevel info
```

- Mehrere Verzeichnisse freigeben: einfach als weitere Positionsargumente an
  `server-filesystem` anhängen (ein Prozess kann mehrere Wurzeln verwalten).
- **Gotcha:** Über Git-Bash/MSYS wird `/mcp` fälschlich als Windows-Pfad
  umgeschrieben (z. B. zu `D:/Tools/Git/mcp`) — MSYS-Pfadkonvertierung. In
  **PowerShell** ausführen, nicht in Git Bash.
- **Gotcha:** Der Server läuft im *stateless*-Modus (siehe `[supergateway]
  Running stateless server` im Log). Ein rohes HTTP-GET auf `/mcp` (z. B.
  schnell mit `curl`/`Invoke-WebRequest` getestet) hat den Prozess in einer
  Session zum Absturz gebracht — Port weg, Task zeigte `LastTaskResult: 0`
  (sauberer Exit, kein von Windows erkannter Fehler, also auch kein
  Auto-Restart durch den Task). Für reine Erreichbarkeits-Checks nur einen
  **TCP-Connect** verwenden, nie ein GET — z. B.
  `Test-NetConnection -ComputerName <ip> -Port 8931` (Windows) oder
  `bash -c 'echo > /dev/tcp/<ip>/8931'` (Linux/VPS). Ein echter
  Funktionstest läuft über `hermes mcp test laptop_fs` (valider
  MCP-JSON-RPC-Handshake statt rohem GET).

Lokal testen (Initialize-Request):

```powershell
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}}}'
Invoke-WebRequest -Uri "http://localhost:8931/mcp" -Method Post -Body $body -ContentType "application/json" -Headers @{Accept="application/json, text/event-stream"} -UseBasicParsing
# → serverInfo.name: "secure-filesystem-server"
```

---

## Setup — Windows-Firewall

**Wichtiger Gotcha:** Windows Firewall lässt eine **Block-Regel immer gegen
eine Allow-Regel gewinnen**, unabhängig davon, wie spezifisch die Allow-Regel
ist. Eine Kombination aus "Allow 100.64.0.0/10" + "Block Any" blockiert damit
**alles**, auch den eigentlich erlaubten Tailscale-Traffic (live erlebt und
per Verbindungstest bestätigt).

**Richtig:** nur eine einzige, scoped Allow-Regel — ohne passende Regel
blockiert Windows Firewall Eingehendes ohnehin standardmäßig:

```powershell
New-NetFirewallRule -DisplayName "Hermes-MCP-Laptop (Tailscale only)" -Direction Inbound -Protocol TCP -LocalPort 8931 -RemoteAddress 100.64.0.0/10 -Action Allow
```

Falls versehentlich zusätzlich eine Block-Regel angelegt wurde, entfernen:

```powershell
Remove-NetFirewallRule -DisplayName "Hermes-MCP-Laptop (Block others)"
```

`100.64.0.0/10` ist der CGNAT-Adressraum, den Tailscale für alle Tailnet-IPs
verwendet — der Filter greift unabhängig davon, welche IP der Laptop bei einem
Tailscale-Reconnect zugewiesen bekommt.

---

## Setup — Persistenz (Scheduled Task)

Läuft nur, solange der auslösende Prozess lebt — für dauerhaften Betrieb ein
Scheduled Task, der bei jeder Anmeldung automatisch startet.

**1. Launcher-Skript** — `C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp.ps1`
(bewusst **außerhalb** von `D:\projects\Hermes` abgelegt, damit der Server
nicht sein eigenes Startskript über die freigegebenen Tools sehen/ändern kann).
Da der Task `-WindowStyle Hidden` läuft, geht stdout/stderr sonst spurlos
verloren — deshalb wird alles zusätzlich in eine Log-Datei geschrieben (wichtig
für die Post-Mortem-Analyse, falls der Prozess mal abstürzt, siehe Gotcha oben):

```powershell
$logFile = "$PSScriptRoot\supergateway.log"
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') --- starting supergateway ---" | Out-File -FilePath $logFile -Append -Encoding utf8
npx -y supergateway --stdio "npx -y @modelcontextprotocol/server-filesystem D:\projects\Hermes" --outputTransport streamableHttp --port 8931 --streamableHttpPath /mcp --logLevel info 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
"$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') --- supergateway exited with code $LASTEXITCODE ---" | Out-File -FilePath $logFile -Append -Encoding utf8
```

**Gotcha:** einfaches `*>> $logFile`-Redirection erzeugte auf diesem Rechner
eine Datei mit falscher/gemischter Encoding (jedes Zeichen durch ein
Leerzeichen getrennt beim Lesen als UTF-8). Fix: explizit durch `Out-File
-Encoding utf8` pipen statt `*>>` zu verwenden.

**2. Hidden-Wrapper** — `C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp-hidden.vbs`.
**Gotcha (2026-07-31):** `powershell.exe -WindowStyle Hidden` als direkte
Task-Aktion allokiert kurz ein Konsolenfenster und versteckt es erst danach —
bei einer Anmeldung nach Neustart kann das als kurz aufblitzendes, leeres
PowerShell-Fenster sichtbar werden (live beobachtet). Ein VBScript-Wrapper
mit `WScript.Shell.Run(..., 0, False)` erzeugt dagegen von Anfang an gar kein
Fenster:

```vbscript
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp.ps1""", 0, False
```

**3. Task registrieren** (Aktion zeigt auf `wscript.exe`, nicht mehr direkt
auf `powershell.exe`):

```powershell
$action = New-ScheduledTaskAction -Execute "wscript.exe" -Argument '"C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp-hidden.vbs"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "HermesLaptopFilesystemMCP" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Description "MCP filesystem server for Hermes-Agent, scoped to D:\projects\Hermes, reachable via Tailscale on port 8931" -Force
```

`ExecutionTimeLimit = Zero` ist wichtig — ohne das killt der Task Scheduler
den Prozess nach dem Default-Limit (3 Tage).

Verifiziert (2026-07-31): laufende Prozesskette nach Neustart über den
Wrapper geprüft — `Get-Process ... | Where MainWindowHandle -ne 0` liefert
für keinen der beteiligten Prozesse (`wscript`/`powershell`/`cmd`/`node`)
einen Treffer.

---

## Setup — Hermes-Registrierung

**Nicht** `config.yaml` direkt editieren — bei diesem (Hostinger-verwalteten)
Hermes-Image überlebt das keinen Container-Neustart (siehe
[hermes-dashboard-remote-setup.md](hermes-dashboard-remote-setup.md) und
Troubleshooting unten). Stattdessen Hermes' eigenes CLI-Tooling:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp add laptop_fs \
  --url http://100.99.233.106:8931/mcp --connect-timeout 10
```

Der Befehl fragt interaktiv nach Auth (mit "n" beantworten — Tailscale +
Firewall übernehmen die Zugriffskontrolle) und nach Tool-Freigabe (mit "y"
alle 14 Tools freigeben). Ohne TTY (z. B. über eine SSH-Einzeiler-Pipe) beides
per stdin mitliefern:

```bash
printf 'n\ny\n' | docker exec -i hermes-agent-7qpk-hermes-agent-1 hermes mcp add laptop_fs --url http://100.99.233.106:8931/mcp --connect-timeout 10
```

Prüfen:

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp list
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test laptop_fs
```

---

## Setup — Auto-Recovery-Watchdog (VPS)

**Problem:** Der Hermes-Gateway-Prozess verbindet sich offenbar nur einmal
beim eigenen Start mit seinen MCP-Servern und reconnectet nicht automatisch,
wenn eine Verbindung abreißt — egal ob durch einen neu registrierten Server
oder durch einen Laptop-Neustart (Details siehe
[Troubleshooting](#troubleshooting)). Ein frischer `hermes mcp test
laptop_fs` zeigt dabei trotzdem `✓ Connected`, weil der einen **neuen**
Verbindungsversuch macht — das beweist nicht, dass der langlebige
Gateway-Prozess (WhatsApp, Dashboard, …) den Server auch nutzen kann. Ohne
Gegenmaßnahme bräuchte es nach *jedem* Laptop-Neustart einen manuellen
`docker restart` des Hermes-Containers.

**Lösung:** Ein Cronjob auf der VPS erkennt automatisch den Übergang
„Laptop war unerreichbar → ist jetzt wieder erreichbar“ und stößt dann genau
einmal den Container-Neustart an.

Bewusst **kein** natives Docker-`HEALTHCHECK` + `restart:`-Policy in der
`docker-compose.yml`, aus zwei Gründen:
- Docker-Restart-Policies reagieren nur auf Container-*Exit*, nicht auf
  einen fehlgeschlagenen Healthcheck — dafür bräuchte es zusätzlich einen
  Sidecar-Container mit Zugriff auf `/var/run/docker.sock` (faktisch
  Root-Zugriff auf den ganzen Host).
- Der Trigger muss **flankenbasiert** sein (nur beim Übergang down→up neu
  starten) — sonst würde der Container die ganze Nacht alle paar Minuten
  sinnlos neu gestartet, während der Laptop einfach zu/aus ist.

**1. Watchdog-Skript** — `/root/hermes-laptop-fs-watchdog.sh` auf der VPS:

```bash
#!/bin/bash
set -uo pipefail

HOST="100.99.233.106"
PORT="8931"
STATE_FILE="/root/.laptop_fs_watchdog_state"
LOG_FILE="/var/log/hermes-laptop-fs-watchdog.log"
CONTAINER="hermes-agent-7qpk-hermes-agent-1"

# Reiner TCP-Connect -- kein HTTP-GET (siehe Gotcha bei "Setup --
# MCP-Filesystem-Server": GET hat den stateless supergateway-Prozess in
# einer Session zum Absturz gebracht).
if timeout 5 bash -c "echo > /dev/tcp/$HOST/$PORT" 2>/dev/null; then
  CURR="up"
else
  CURR="down"
fi

PREV="unknown"
[ -f "$STATE_FILE" ] && PREV=$(cat "$STATE_FILE")
echo "$CURR" > "$STATE_FILE"

TS=$(date -u '+%Y-%m-%dT%H:%M:%SZ')
if [ "$PREV" = "down" ] && [ "$CURR" = "up" ]; then
  echo "$TS laptop_fs recovered ($PREV -> $CURR), restarting $CONTAINER" >> "$LOG_FILE"
  docker restart "$CONTAINER" >> "$LOG_FILE" 2>&1
elif [ "$PREV" != "$CURR" ]; then
  echo "$TS state changed: $PREV -> $CURR" >> "$LOG_FILE"
fi
```

**2. Als Cronjob installieren** (alle 2 Minuten):

```bash
chmod +x /root/hermes-laptop-fs-watchdog.sh
(crontab -l 2>/dev/null; echo '*/2 * * * * /root/hermes-laptop-fs-watchdog.sh') | crontab -
```

**Verifiziert (2026-07-30):** Zustand künstlich auf `down` gesetzt und
Skript erneut laufen lassen → hat einen echten `docker restart` ausgelöst
(Container-`StartedAt` änderte sich); ein anschließender Lauf im
`up`-Zustand löste **keinen** weiteren Neustart aus (kein Flap-Loop).

Logs prüfen:

```bash
tail -f /var/log/hermes-laptop-fs-watchdog.log
```

---

## Chat-Beispiele / Verifikation

Direkt getestet über Hermes' Non-Interactive-Modus (`hermes chat -q`), genauso
nutzbar über WhatsApp oder das Dashboard:

**Datei erstellen (Positivtest):**
> „Nutze das laptop_fs-Tool. Rufe zuerst list_allowed_directories auf, dann
> erstelle in diesem Verzeichnis eine Datei namens test.txt mit dem Inhalt
> 'Testdatei von Hermes'. Nenne mir danach den vollen Pfad der erstellten
> Datei.“

→ Ergebnis: `D:\projects\Hermes\test.txt` wurde angelegt, lokal auf dem
Laptop verifiziert.

**Zugriff außerhalb (Negativtest):**
> „Nutze das laptop_fs-Tool. Versuche die Datei C:\Windows\win.ini zu lesen
> (also außerhalb des erlaubten Verzeichnisses). Was passiert, und was sagt
> die Fehlermeldung genau?“

→ Ergebnis: abgelehnt, noch vor jedem echten Dateisystemzugriff:
```
Access denied - path outside allowed directories: C:\Windows\win.ini not in D:\projects\Hermes
```

Weitere Beispiel-Prompts:
- „Zeig mir alle Dateien im laptop_fs-Verzeichnis.“
- „Lies mir die Datei D:\projects\Hermes\... vor.“
- „Lege im laptop_fs-Verzeichnis einen Unterordner Notizen an.“

---

## Betrieb

```powershell
# Status des Scheduled Task
Get-ScheduledTask -TaskName "HermesLaptopFilesystemMCP" | Get-ScheduledTaskInfo

# Manuell (neu) starten
Start-ScheduledTask -TaskName "HermesLaptopFilesystemMCP"

# Deaktivieren / wieder aktivieren
Disable-ScheduledTask -TaskName "HermesLaptopFilesystemMCP"
Enable-ScheduledTask -TaskName "HermesLaptopFilesystemMCP"

# Manueller Start ohne Task (Fenster muss offen bleiben)
powershell.exe -File "C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp.ps1"

# Von der VPS aus testen
docker exec hermes-agent-7qpk-hermes-agent-1 hermes mcp test laptop_fs

# Auto-Recovery-Watchdog: Log ansehen / Cronjob prüfen (auf der VPS)
tail -20 /var/log/hermes-laptop-fs-watchdog.log
crontab -l
```

---

## Troubleshooting

| Symptom | Ursache & Lösung |
|---|---|
| `hermes mcp test laptop_fs` → Connection failed | Laptop aus/im Schlafmodus, Scheduled Task nicht gestartet, oder Tailscale auf einem der beiden Geräte nicht verbunden (`tailscale status` prüfen). |
| Connection failed **trotz** laufendem Server | Windows-Firewall-Regel prüfen — eine zusätzliche "Block Any"-Regel blockiert auch den erlaubten Traffic (siehe [Firewall-Gotcha](#setup--windows-firewall)). |
| `EADDRINUSE: address already in use :::8931` beim Neustart | Alter `node`-Prozess hält den Port noch (z. B. nach Abbruch der Shell, die den Prozess gestartet hat — Beenden der Shell killt nicht zwangsläufig den dahinter gestarteten `npx`/`node`-Kindprozess). Prozess über `Get-NetTCPConnection -LocalPort 8931` finden und `Stop-Process -Force` beenden. |
| `/mcp`-Pfad wird zu einem Windows-Pfad umgeschrieben (z. B. `D:/Tools/Git/mcp`) | MSYS/Git-Bash-Pfadkonvertierung. In PowerShell statt Git Bash starten. |
| Config-Änderungen an `laptop_fs` verschwinden nach Hermes-Neustart | Nicht `config.yaml` direkt editieren — `hermes mcp add/remove/configure` verwenden (siehe [Setup — Hermes-Registrierung](#setup--hermes-registrierung)). |
| Hermes verweigert einen Pfad, der eigentlich erlaubt sein sollte | Pfad exakt gegen `list_allowed_directories` prüfen (Groß-/Kleinschreibung, Backslashes) — der Filesystem-Server vergleicht strikt gegen die konfigurierte Wurzel. |
| `hermes mcp test laptop_fs` von der VPS zeigt ✓, aber WhatsApp/Dashboard nutzt das Tool trotzdem nicht | Der langlebige Gateway-Prozess reconnectet nicht automatisch nach einer unterbrochenen Verbindung (neu registrierter Server, Laptop-Neustart, Netzwerk-Blip). Ein frischer CLI-Test beweist nur, dass der Server erreichbar ist — nicht, dass der laufende Gateway-Prozess ihn nutzt. Fix: `docker restart hermes-agent-7qpk-hermes-agent-1` (seit 2026-07-30 automatisiert, siehe [Auto-Recovery-Watchdog](#setup--auto-recovery-watchdog-vps)). Zur Bestätigung `docker inspect -f '{{.State.StartedAt}}' hermes-agent-7qpk-hermes-agent-1` gegen den Zeitpunkt des letzten Ausfalls vergleichen. |
| Supergateway-Prozess verschwindet ohne Vorwarnung (Port 8931 lauscht nicht mehr, Task zeigt `LastTaskResult: 0`) | Vermutlich ausgelöst durch ein rohes HTTP-GET gegen den *stateless* Endpoint (z. B. beim manuellen Testen mit `curl`/`Invoke-WebRequest`). Für Erreichbarkeits-Checks nur TCP-Connect verwenden, nie GET (siehe Gotcha bei [MCP-Filesystem-Server-Setup](#setup--mcp-filesystem-server-laptop)). Seit 2026-07-30 wird die Prozessausgabe nach `C:\Users\steve\hermes-laptop-mcp\supergateway.log` geloggt — dort zuerst nachsehen. |
| Kurz aufblitzendes leeres PowerShell-Fenster nach Laptop-Neustart | `powershell.exe -WindowStyle Hidden` als direkte Task-Aktion versteckt das Konsolenfenster erst *nach* dem Erzeugen — bei der Anmeldung kann der Flash sichtbar werden. Seit 2026-07-31 startet der Task stattdessen über einen VBScript-Wrapper (`wscript.exe start-filesystem-mcp-hidden.vbs`, `WScript.Shell.Run(..., 0, False)`), der von Anfang an kein Fenster erzeugt (siehe [Persistenz-Setup](#setup--persistenz-scheduled-task)). |

---

## Sicherheit

- Kein öffentlich erreichbarer Port — Port 8931 ist nur über das
  Tailscale-VPN (gegenseitig authentifiziertes WireGuard) und zusätzlich per
  Windows-Firewall auf `100.64.0.0/10` beschränkt erreichbar.
- Kein Bearer-Token nötig/konfiguriert — die Netzwerk-Ebene (Tailscale +
  Firewall) ist hier die Zugriffskontrolle, nicht ein Secret im Request.
- Der Filesystem-MCP-Server erzwingt zusätzlich eine harte Verzeichnis-
  Whitelist auf Anwendungsebene — selbst bei einem (theoretischen) Netzwerk-
  Fehlkonfigurations-Fall bleibt der Zugriff auf `D:\projects\Hermes`
  beschränkt. Negativtest verifiziert (siehe oben).
- **Bekannte Grenze:** Hermes bekommt volle Lese-/Schreibrechte (`write_file`,
  `edit_file`, `move_file`, …) auf das gesamte freigegebene Verzeichnis, nicht
  nur Lesezugriff. Für rein lesende Zwecke könnte `tools: include:` in der
  Hermes-Config auf die Read-Tools eingeschränkt werden — aktuell bewusst
  nicht gemacht, da Schreibzugriff gewünscht ist.
- Das MCP-"Roots"-Protokoll kann bei manchen Clients die Kommandozeilen-
  Verzeichnis-Whitelist überschreiben (bekanntes Upstream-Verhalten von
  `@modelcontextprotocol/server-filesystem`). Nach Updates von Hermes oder dem
  Filesystem-Server den Negativtest oben erneut ausführen.

---

## Wartung & Grenzfälle

- **Laptop-Neustart:** Lokal startet der Scheduled Task den
  Supergateway-Prozess automatisch bei der nächsten Anmeldung neu. Der
  Hermes-Gateway-Prozess auf der VPS braucht nach jeder Downtime zusätzlich
  einen eigenen Neustart, um die Verbindung neu aufzubauen — das übernimmt
  seit 2026-07-30 automatisch der
  [Auto-Recovery-Watchdog](#setup--auto-recovery-watchdog-vps), kein
  manuelles Eingreifen mehr nötig.
- **Laptop offline/im Schlafmodus:** `laptop_fs`-Tool-Aufrufe laufen einfach
  in den Timeout und schlagen fehl; Hermes bricht nicht ab, das Verzeichnis
  ist dann schlicht vorübergehend nicht erreichbar.
- **Weiteres Verzeichnis freigeben:** in
  `C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp.ps1` den Pfad als
  weiteres Positionsargument an `server-filesystem` anhängen, Task neu
  starten (`Start-ScheduledTask` nach vorherigem Stop, oder Laptop neu
  anmelden).
- **Tailscale-IP ändert sich** (z. B. nach Geräte-Neuregistrierung): mit
  `hermes mcp remove laptop_fs` und erneutem `hermes mcp add` mit der neuen
  IP neu registrieren.
- **Hermes-Container-Neubau:** MCP-Registrierung geht dabei nicht verloren,
  solange `~/.hermes` (→ `/opt/data`) auf dem VPS erhalten bleibt (dort liegt
  `config.yaml`, in die `hermes mcp add` schreibt).

---

*Erstellt 2026-07-29, zuletzt aktualisiert 2026-07-31 (VBScript-Hidden-Wrapper
gegen PowerShell-Fenster-Flash; 2026-07-30: Auto-Recovery-Watchdog +
Logging). Betrifft Laptop `gpdsteve` (100.99.233.106) und Host
`srv1608402.hstgr.cloud` (100.110.206.80), Hermes-Container
`hermes-agent-7qpk-hermes-agent-1`.*
