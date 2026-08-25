# Hermes Voice — Twilio Inbound Voice Integration

Telefonie-Gateway für den Hermes Agent: Du rufst eine Twilio-Nummer an, sprichst
auf Deutsch, und Hermes antwortet dir live mit einer gesprochenen Antwort. Der
Gesprächskontext bleibt über den gesamten Anruf erhalten. Läuft **parallel** zum
bestehenden WhatsApp-Kanal und ersetzt nichts.

> Status: **Live & produktiv** (seit 2026-07-17)
> Nummer: **+43 1 4351876**
> Architektur zuletzt überarbeitet: **2026-08-25** — läuft jetzt vollständig aus
> `/opt/data` (dem persistenten Hermes-Datenverzeichnis) statt teilweise aus dem
> Container-Dateisystem, und übersteht damit ein Hermes-Agent-Image-Update ohne
> manuellen Eingriff. Details unter [Wartung & Grenzfälle](#wartung--grenzfälle).

---

## Inhalt

- [Überblick](#überblick)
- [Architektur](#architektur)
- [Anruf-Ablauf](#anruf-ablauf)
- [Komponenten & Dateien](#komponenten--dateien)
- [Konfiguration](#konfiguration)
- [Deployment](#deployment)
  - [1. Voice-Server (persistentes Datenverzeichnis)](#1-voice-server-persistentes-datenverzeichnis)
  - [2. Autostart](#2-autostart)
  - [3. Traefik-Routing (File-Provider)](#3-traefik-routing-file-provider)
  - [4. Twilio-Konfiguration](#4-twilio-konfiguration)
- [Verifikation](#verifikation)
- [Betrieb](#betrieb)
- [Troubleshooting](#troubleshooting)
- [Sicherheit](#sicherheit)
- [Wartung & Grenzfälle](#wartung--grenzfälle)

---

## Überblick

| Aspekt          | Entscheidung |
|-----------------|--------------|
| **STT** (Spracherkennung) | Ausschließlich Twilios eingebautes `<Gather input="speech">` (de-DE). Schnell, keine GPU, geringe Latenz. Kein lokales Fallback (vereinfacht ggü. früherer Planung — `stt.py` wurde nie gebraucht und existiert nicht). |
| **TTS** (Sprachausgabe)   | `edge-tts` mit Stimme `de-AT-JonasNeural` (Österreichisch, männlich). |
| **Agent**       | Ruft die Hermes-CLI auf (volle Konfiguration: Modell, Tools, Skills, Memory), asynchron in einem Hintergrund-Thread. |
| **Kontext**     | Pro Anruf über die Twilio `CallSid` → Hermes-Session (`--resume`). |
| **Warteschleife** | Während Hermes antwortet, hört der Anrufer Hold-Musik statt zu warten — vermeidet Twilios ~15s-Webhook-Limit komplett (siehe [Anruf-Ablauf](#anruf-ablauf)). |
| **Routing**     | Traefik File-Provider (kein Eingriff am Hermes-Container). |
| **TLS**         | Let's Encrypt via Traefik (bestehender certResolver). |
| **Sicherheit**  | Twilio-Signaturvalidierung (`X-Twilio-Signature`) auf jedem Webhook, plus ein Honeypot-Gate gegen Scanner/Bots (siehe [Sicherheit](#sicherheit)). |

---

## Architektur

```
Anrufer (Telefon)
   │
   ▼
Twilio (PSTN → Webhook)
   │  POST https://srv1608402.hstgr.cloud/voice/inbound
   ▼
Traefik  (host-Netz, TLS-Terminierung, File-Provider-Route /voice/*)
   │  http://172.16.1.2:8765
   ▼
Hermes Voice Server  (FastAPI, im Hermes-Container, Port 8765)
   ├── STT:   Twilio SpeechResult (de-DE)
   ├── Agent: hermes chat  (Kontext pro CallSid, Hintergrund-Thread)
   └── TTS:   edge-tts → MP3  (de-AT-JonasNeural)
   │  TwiML <Play> + <Gather> (oder Hold-Loop, siehe unten)
   ▼
Twilio spielt die Audio-Antwort ab → Anrufer hört Hermes
```

Der Voice-Server läuft **im selben Container** wie der Hermes-Agent und der
WhatsApp-Bot, als eigener Prozess auf Port 8765 — sein Code liegt aber, anders
als der restliche Hermes-Agent, unter `/opt/data/hermes_voice/`, also im
persistenten Datenverzeichnis (siehe [Komponenten & Dateien](#komponenten--dateien)).
Traefik läuft im `network_mode: host` und erreicht den Server über die
Bridge-IP des Hermes-Containers.

---

## Anruf-Ablauf

1. **Eingehender Anruf** — Twilio ruft `POST /voice/inbound` auf.
   Der Server begrüßt (TTS) und öffnet ein `<Gather input="speech">`.
2. Twilio transkribiert die Sprache und schickt den Text an `POST /voice/respond`.
   Der Server startet die Hermes-Anfrage **in einem Hintergrund-Thread** und
   antwortet Twilio sofort — entweder direkt mit der Antwort (falls sie
   innerhalb von `VOICE_QUICK_REPLY_WINDOW` fertig ist) oder mit Hold-Musik
   (`hold_ocean.mp3`) plus einem Redirect auf `POST /voice/poll`.
3. `/voice/poll` fragt, ob die Hermes-Antwort inzwischen fertig ist:
   - **Ja** → TTS-MP3 erzeugen, `<Play>` + erneutes `<Gather>` (nächste
     Gesprächsrunde).
   - **Nein** → nochmal Hold-Musik + Redirect auf `/voice/poll` (Runde+1),
     bis `VOICE_HOLD_MAX_ROUNDS` erreicht ist (dann Abbruch mit Entschuldigung).

   Damit läuft der Server nie gegen Twilios ~15-Sekunden-Webhook-Limit —
   jeder Webhook antwortet praktisch sofort, egal wie lange Hermes für die
   eigentliche Antwort braucht.
4. Sagt der Anrufer „auf Wiederhören" o. Ä., wird verabschiedet und aufgelegt.
5. Nach Anrufende räumt `POST /voice/status` die Session auf.

---

## Komponenten & Dateien

Alle Dateien liegen unter `/opt/data/hermes_voice/` (Eigentümer `hermes:hermes`)
— **innerhalb** des persistenten `$HERMES_HOME`-Bind-Mounts
(`/docker/hermes-agent-7qpk/data` auf dem Host), **nicht** im
Container-Dateisystem. Das ist bewusst so: so übersteht der komplette
Voice-Stack ein Hermes-Agent-Image-Update, ohne dass irgendetwas neu
angelegt werden muss (siehe [Wartung & Grenzfälle](#wartung--grenzfälle)).

| Datei | Zweck |
|-------|-------|
| `voice_server.py` | FastAPI-App mit den Voice-Endpoints |
| `config.py`        | Zentrale Konfiguration (liest `~/.hermes/.env`) |
| `tts.py`            | Text-to-Speech via edge-tts |
| `agent.py`          | Hermes-CLI-Aufruf (`$HERMES_BIN`) mit Session-Kontext pro CallSid, läuft im Hintergrund-Thread |
| `honeypot.py`       | Blockt Scanner/Bots, die auf illegitimen Pfaden landen (`/.env`, `/wp-login.php`, `/admin`, …): persistente IP-Blockliste, Sofort-Block beim ersten Treffer, gedrosselte WhatsApp-Meldung |
| `watchdog.sh`       | Health-Check + Neustart bei Bedarf; wird alle 5 Min. per Hermes-Cron-Job aufgerufen (siehe [Autostart](#2-autostart)) — der reguläre Weg, den Server neu zu starten |
| `.venv/`            | Eigenes venv (u. a. `fastapi`, `uvicorn`, `twilio`, `edge-tts`, `httpx`, `python-multipart` — direkt installiert, kein `requirements.txt`) |

### HTTP-Endpoints

| Methode & Pfad | Zweck |
|----------------|-------|
| `POST /voice/inbound`   | Anrufeinstieg — bei Twilio konfiguriert |
| `POST /voice/respond`   | Empfängt Speech-Transkript, startet die Hermes-Anfrage im Hintergrund |
| `POST /voice/poll`      | Warteschleifen-Polling: liefert die Antwort sobald fertig, sonst weitere Hold-Runde |
| `POST /voice/status`    | Call-Status-Callback (Session-Cleanup) |
| `GET  /voice/audio/{n}` | Liefert die TTS-/Hold-MP3 an Twilio |
| `GET  /voice/health`    | Health-Check (ohne Auth) |

Alles außerhalb dieser sechs Routen wird von der Honeypot-Middleware als
Scanner/Bot gewertet — siehe [Sicherheit](#sicherheit).

---

## Konfiguration

Alle Werte über Umgebungsvariablen (Defaults in `config.py`). Twilio-Credentials
liegen in `~/.hermes/.env` (`HOME=/opt/data/home`), Datei-Modus `600`, Eigentümer
`hermes:hermes`.

| Variable | Default | Bedeutung |
|----------|---------|-----------|
| `TWILIO_ACCOUNT_SID`     | – | Twilio Account SID (`AC…`) |
| `TWILIO_AUTH_TOKEN`      | – | Twilio Auth Token (Secret) |
| `TWILIO_PHONE_NUMBER`    | – | Nummer im E.164-Format |
| `VOICE_PUBLIC_BASE_URL`  | `https://srv1608402.hstgr.cloud` | **Muss** exakt der Twilio-Webhook-URL entsprechen (Signaturprüfung!) |
| `VOICE_HOST`             | `0.0.0.0` | Bind-Adresse |
| `VOICE_PORT`             | `8765` | Interner Port |
| `VOICE_TTS_VOICE`        | `de-AT-JonasNeural` | edge-tts-Stimme |
| `VOICE_TTS_RATE`         | `+0%` | edge-tts-Sprechgeschwindigkeit |
| `VOICE_SPEECH_LANGUAGE`  | `de-DE` | Erkennungssprache |
| `VOICE_AUDIO_DIR`        | `/opt/data/voice_audio` | Ablage für generierte TTS-/Hold-Clips (persistent) |
| `HERMES_BIN`             | `/opt/hermes/.venv/bin/hermes` | Pfad zur Hermes-CLI (liegt im Image, nicht in `/opt/data` — bei jedem Image-Update automatisch die neue Binary) |
| `VOICE_VALIDATE_SIGNATURE` | `1` | Signaturprüfung (für lokale Tests `0`) |
| `VOICE_AGENT_TIMEOUT`    | `60` | Sekunden bis der Hintergrund-Agent-Aufruf aufgegeben wird. Kein Twilio-Zeitdruck mehr dank Hold-Loop — daher deutlich höher als früher (war `12`). |
| `VOICE_HOLD_AUDIO_FILENAME` | `hold_ocean.mp3` | Hold-Clip, muss in `VOICE_AUDIO_DIR` liegen |
| `VOICE_HOLD_CLIP_SECONDS`   | `6` | Ungefähre Länge des Hold-Clips (steuert die Rundenzahl) |
| `VOICE_QUICK_REPLY_WINDOW`  | `4` | Antwortet Hermes schneller als dies, wird die Hold-Schleife übersprungen |
| `VOICE_HOLD_MAX_ROUNDS`     | berechnet aus den drei Werten oben | Sicherheitsnetz: maximale Hold-Runden, bevor abgebrochen wird |

---

## Deployment

### 1. Voice-Server (persistentes Datenverzeichnis)

```bash
cd /opt/data/hermes_voice
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python \
  fastapi "uvicorn[standard]" twilio edge-tts httpx python-multipart
chown -R hermes:hermes /opt/data/hermes_voice
```

Twilio-Credentials in `~/.hermes/.env` ablegen (Eigentümer `hermes`, Modus `600`):

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+431XXXXXXX
```

Wichtig: `/opt/data/hermes_voice/` liegt im Bind-Mount
(`/docker/hermes-agent-7qpk/data:/opt/data`), **nicht** im Container selbst.
Ein Container-Rebuild (z. B. durch ein Hermes-Agent-Image-Update) lässt dieses
Verzeichnis unangetastet — der einmalige Setup-Schritt oben muss nur nach
einem echten Datenverlust (neues/leeres `/opt/data`) wiederholt werden, nicht
bei jedem Image-Update.

### 2. Autostart

Der Autostart läuft über einen **nativen Hermes-Cron-Job**, nicht über einen
Eingriff in `/entrypoint.sh` (das war der ursprüngliche Ansatz und der Grund,
warum ein früheres Hermes-Agent-Update die Telefonie lahmgelegt hat — der
Cron-Job liegt dagegen in `/opt/data/cron/jobs.json`, also ebenfalls
persistent):

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes cron create \
  --name voice-server-watchdog \
  --script voice_watchdog.sh \
  --no-agent \
  --schedule "*/5 * * * *"
```

Der Job ruft alle 5 Minuten `watchdog.sh` auf: läuft der Prozess bereits und
antwortet der Health-Check mit `200`, passiert nichts (stiller Cron-Lauf dank
`--no-agent`). Läuft er nicht oder antwortet nicht, wird er neu gestartet —
und die kurze Meldung dazu kommt automatisch als WhatsApp-Nachricht (`deliver:
origin`), nicht nur ins Log.

> Für eine sofortige Prüfung/Neustart ohne auf den nächsten 5-Minuten-Tick zu
> warten (z. B. direkt nach einem manuellen Container-Neustart):
> ```bash
> docker exec hermes-agent-7qpk-hermes-agent-1 bash /opt/data/scripts/voice_watchdog.sh
> ```

### 3. Traefik-Routing (File-Provider)

Traefik läuft als Compose-Service unter `/docker/traefik/` und nutzt zunächst nur
den Docker-Label-Provider. Für die Voice-Route wird der **File-Provider ergänzt**,
ohne den Hermes-Container anzufassen.

**a) Dynamic-Config anlegen** (`/docker/traefik/dynamic/voice.yml`):

```yaml
http:
  routers:
    hermes-voice:
      rule: "Host(`srv1608402.hstgr.cloud`) && PathPrefix(`/voice`)"
      entryPoints:
        - websecure
      service: hermes-voice
      tls:
        certResolver: letsencrypt
  services:
    hermes-voice:
      loadBalancer:
        servers:
          - url: "http://172.16.1.2:8765"   # Bridge-IP des Hermes-Containers
```

**b) `docker-compose.yml` ergänzen** (unter `command:` und `volumes:`):

```yaml
    command:
      # ... bestehende Flags ...
      - --providers.file.directory=/etc/traefik/dynamic
      - --providers.file.watch=true
    volumes:
      # ... bestehende Mounts ...
      - /docker/traefik/dynamic:/etc/traefik/dynamic:ro
```

**c) Recreate** (Projektname zwingend, sonst wird ein falsches Projekt/Volume erstellt):

```bash
cd /docker/traefik
docker compose -p traefik up -d
```

Das benannte Volume `traefik_traefik-letsencrypt` (Zertifikate) bleibt dabei
erhalten. Docker-Label- und File-Provider laufen parallel; bestehende Routen
bleiben unberührt.

### 4. Twilio-Konfiguration

In der Twilio-Konsole für die Nummer unter **Voice — A CALL COMES IN**:

- **Webhook:** `https://srv1608402.hstgr.cloud/voice/inbound`
- **Methode:** `HTTP POST`

---

## Verifikation

```bash
# Backend aus Traefiks Sicht (host-Netz)
curl -s http://172.16.1.2:8765/voice/health

# Von außen über HTTPS (wie Twilio)
curl -s https://srv1608402.hstgr.cloud/voice/health
# → {"status":"ok"}

# Signaturprüfung aktiv? (ohne gültige Signatur muss 403 kommen)
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  https://srv1608402.hstgr.cloud/voice/inbound -d "CallSid=x"
# → 403

# Echter Testanruf
# +43 1 4351876 anrufen → Begrüßung hören → Frage stellen → Antwort hören
```

Zusätzlich, nach einem Container-Neustart oder Image-Update — prüfen, ob die
Hermes-CLI selbst noch erreichbar/aufrufbar ist (worauf `agent.py` letztlich
angewiesen ist):

```bash
docker exec hermes-agent-7qpk-hermes-agent-1 hermes --version
```

---

## Betrieb

```bash
# Live-Log verfolgen
tail -f /opt/data/logs/voice.log

# Relevante Log-Zeilen im Gespräch:
#   "Eingehender Anruf ..."   → Twilio hat verbunden
#   "Nutzer sagte: ..."       → Spracherkennungs-Ergebnis
#   "Hermes antwortet: ..."   → generierte Antwort

# Regulärer Weg für einen manuellen Neustart: der Watchdog selbst
# (idempotent — killt einen hängenden Prozess nur, wenn der Health-Check
# fehlschlägt, startet sonst sauber neu)
docker exec hermes-agent-7qpk-hermes-agent-1 bash /opt/data/scripts/voice_watchdog.sh
```

---

## Troubleshooting

| Symptom | Ursache & Lösung |
|---------|------------------|
| Anruf gibt Fehlerton | Voice-Server läuft nicht — `docker exec ... pgrep -fa voice_server.py`; Health prüfen; notfalls `watchdog.sh` manuell aufrufen (siehe [Betrieb](#betrieb)) statt auf den 5-Minuten-Cron zu warten. |
| `/voice/health` extern 404 | Traefik-File-Provider nicht aktiv oder `voice.yml`-Syntax falsch (Backticks in `rule:` prüfen). |
| Webhook liefert 403 | Zwei mögliche Ursachen: (a) `VOICE_PUBLIC_BASE_URL` stimmt nicht exakt mit der Twilio-URL überein (Signaturprüfung schlägt fehl), oder (b) die Anrufer-IP wurde vom Honeypot-Gate geblockt (unwahrscheinlich bei echten Twilio-Anrufen, aber möglich bei Tests von einer bereits geblockten Test-IP aus). |
| Anrufer hört lange Hold-Musik, dann Abbruch | Hermes-Antwort dauert länger als `VOICE_HOLD_MAX_ROUNDS × VOICE_HOLD_CLIP_SECONDS`. Timeout-Werte erhöhen oder Ursache der langsamen Antwort (Tools/Memory) prüfen. |
| Keine Sprachausgabe | edge-tts braucht Internetzugang (Microsoft-Endpoint). |
| `hermes`-User kann `.env` nicht lesen | Eigentümer/Modus prüfen: `chown hermes:hermes ~/.hermes/.env && chmod 600`. |
| Nach einem Hermes-Agent-Image-Update reagiert `/voice/*` gar nicht | Bridge-IP des Containers hat sich geändert — mit `docker inspect hermes-agent-7qpk-hermes-agent-1 --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}'` prüfen und ggf. in `/docker/traefik/dynamic/voice.yml` nachziehen. |

---

## Sicherheit

- **Signaturvalidierung** (`X-Twilio-Signature`) ist standardmäßig aktiv. Requests
  ohne gültige Signatur werden mit `403` abgewiesen.
- Die Signatur wird gegen `VOICE_PUBLIC_BASE_URL` (die externe HTTPS-URL) berechnet,
  nicht gegen die interne HTTP-Adresse — daher muss `VOICE_PUBLIC_BASE_URL` exakt
  der Twilio-Webhook-URL entsprechen.
- **Honeypot-Gate** (`honeypot.py`): der Service kennt genau sechs legitime Routen
  (siehe [HTTP-Endpoints](#http-endpoints)). Jeder Request auf einen nicht
  gematchten Pfad — `/.env`, `/wp-login.php`, `/admin`, Path-Traversal-Versuche,
  falsche Methode auf einer echten Route — gilt per Definition als Scanner/Bot,
  niemals als Twilio. Die IP wird beim ersten Treffer sofort und dauerhaft in
  einer JSON-Datei geblockt (kein "drei Versuche"-Grace), jede weitere Anfrage
  dieser IP bekommt auf jedem Pfad ein flaches `403` — inklusive einer
  gedrosselten WhatsApp-Benachrichtigung.
- Der Auth-Token liegt nur in `~/.hermes/.env` (Modus `600`), nicht im Code, nicht
  in der Doku, nicht im Memory.
- Der Server läuft als unprivilegierter `hermes`-User, niemals als root.

---

## Wartung & Grenzfälle

- **Container-Neustart (`docker restart` / Crash-Recovery):** unkritisch — der
  Cron-Job `voice-server-watchdog` erkennt einen toten Voice-Server spätestens
  beim nächsten 5-Minuten-Tick und startet ihn neu; für eine sofortige
  Wiederherstellung `watchdog.sh` manuell aufrufen (siehe [Betrieb](#betrieb)).
- **Hermes-Container-Neubau / Image-Update (Recreate):** seit der
  Überarbeitung am 2026-08-25 **unkritisch für den Voice-Stack selbst** — Code
  (`/opt/data/hermes_voice/`), venv, Cron-Job-Definition
  (`/opt/data/cron/jobs.json`) und Twilio-Credentials liegen alle im
  persistenten `/opt/data`-Bind-Mount und werden von einem Image-Update nicht
  berührt. Ein Punkt bleibt trotzdem zu prüfen:
  - Die **Bridge-IP** (`172.16.1.2` zum Zeitpunkt dieser Doku) in
    `/docker/traefik/dynamic/voice.yml` — Docker garantiert nicht, dass ein
    neu erstellter Container dieselbe IP im Bridge-Netz bekommt. Nach jedem
    Recreate mit `docker inspect` prüfen und bei Abweichung die Datei
    nachziehen (siehe [Troubleshooting](#troubleshooting)).
  - `$HERMES_BIN` (`/opt/hermes/.venv/bin/hermes`) zeigt bewusst auf den
    Image-eigenen, nicht-persistenten Pfad — das ist korrekt so: nach einem
    Update soll `agent.py` die *neue* Hermes-CLI aufrufen, nicht eine
    eingefrorene alte Kopie.
- **Traefik-Recreate:** immer mit `-p traefik` (korrekter Projektname), sonst wird
  ein falsches Projekt samt leerem acme-Volume erzeugt.
- **Verifizierter Update-Ablauf** fürs Hermes-Agent-Image: vor dem Pull das
  laufende Image lokal taggen (`docker tag
  ghcr.io/hostinger/hvps-hermes-agent:latest
  hvps-hermes-agent:pre-update-<datum>` — sofortiger Rollback-Pfad), dann
  `docker pull` + `docker compose up -d --force-recreate hermes-agent`,
  danach `watchdog.sh` manuell auslösen statt auf den Cron-Tick zu warten,
  und mit den Checks aus [Verifikation](#verifikation) plus `hermes mcp test
  openbrain` (falls die OpenBrain-Integration mitbetroffen sein könnte)
  gegenprüfen. Zuletzt angewendet 2026-08-25 (v0.19.0 → v0.20.5), Telefonie
  danach live per Testanruf bestätigt.

---

*Erstellt 2026-07-17, überarbeitet 2026-08-25 (Architektur auf `/opt/data` +
Hermes-Cron-Watchdog umgestellt, Hold-Loop + Honeypot-Gate ergänzt — Auslöser
war ein Hermes-Agent-Update, das die Telefonie mit der alten
`/opt/hermes/voice` + `/entrypoint.sh`-Architektur lahmgelegt hatte). Betrifft
Host `srv1608402.hstgr.cloud`, Hermes-Container `hermes-agent-7qpk`,
Traefik-Projekt `traefik`.*
