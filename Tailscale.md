# Hermes-Agent ↔ Laptop — Dateizugriff via Tailscale + MCP

Gibt dem Hermes-Agent (läuft im Docker-Container auf der Hostinger-VPS) Lese-/
Schreibzugriff auf **genau ein** Verzeichnis auf einem Laptop — nicht mehr. Der
Laptop ist kein Server (wechselnde IP, NAT, nicht immer online), deshalb läuft
die Verbindung über ein privates Tailscale-VPN statt über eine öffentliche
Portfreigabe.

> Status: **Live** (seit 2026-07-29)
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
nicht sein eigenes Startskript über die freigegebenen Tools sehen/ändern kann):

```powershell
npx -y supergateway --stdio "npx -y @modelcontextprotocol/server-filesystem D:\projects\Hermes" --outputTransport streamableHttp --port 8931 --streamableHttpPath /mcp --logLevel info
```

**2. Task registrieren:**

```powershell
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "C:\Users\steve\hermes-laptop-mcp\start-filesystem-mcp.ps1"'
$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:COMPUTERNAME\$env:USERNAME"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "HermesLaptopFilesystemMCP" -Action $action -Trigger $trigger -Settings $settings -RunLevel Limited -Description "MCP filesystem server for Hermes-Agent, scoped to D:\projects\Hermes, reachable via Tailscale on port 8931" -Force
```

`ExecutionTimeLimit = Zero` ist wichtig — ohne das killt der Task Scheduler
den Prozess nach dem Default-Limit (3 Tage).

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

- **Laptop-Neustart:** Der Scheduled Task startet den Server automatisch bei
  der nächsten Anmeldung neu — kein manuelles Eingreifen nötig.
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

*Erstellt 2026-07-29. Betrifft Laptop `gpdsteve` (100.99.233.106) und Host
`srv1608402.hstgr.cloud` (100.110.206.80), Hermes-Container
`hermes-agent-7qpk-hermes-agent-1`.*
