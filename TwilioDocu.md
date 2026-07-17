# Hermes Voice — Twilio Inbound Voice Integration

Telefonie-Gateway für den Hermes Agent: Du rufst eine Twilio-Nummer an, sprichst
auf Deutsch, und Hermes antwortet dir live mit einer gesprochenen Antwort. Der
Gesprächskontext bleibt über den gesamten Anruf erhalten. Läuft **parallel** zum
bestehenden WhatsApp-Kanal und ersetzt nichts.

> Status: **Live & produktiv** (seit 2026-07-17)
> Nummer: **+43 1 4351876**

---

## Inhalt

- [Überblick](#überblick)
- [Architektur](#architektur)
- [Anruf-Ablauf](#anruf-ablauf)
- [Komponenten & Dateien](#komponenten--dateien)
- [Konfiguration](#konfiguration)
- [Deployment](#deployment)
  - [1. Voice-Server (im Hermes-Container)](#1-voice-server-im-hermes-container)
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
| **STT** (Spracherkennung) | Twilios eingebautes `<Gather input="speech">` (de-DE). Schnell, keine GPU, geringe Latenz. Optional: lokales faster-whisper als Fallback. |
| **TTS** (Sprachausgabe)   | `edge-tts` mit Stimme `de-AT-JonasNeural` (Österreichisch, männlich). |
| **Agent**       | Ruft die Hermes-CLI auf (volle Konfiguration: Modell, Tools, Skills, Memory). |
| **Kontext**     | Pro Anruf über die Twilio `CallSid` → Hermes-Session (`--resume`). |
| **Routing**     | Traefik File-Provider (kein Eingriff am Hermes-Container). |
| **TLS**         | Let's Encrypt via Traefik (bestehender certResolver). |
| **Sicherheit**  | Twilio-Signaturvalidierung (`X-Twilio-Signature`) auf jedem Webhook. |

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
   ├── Agent: hermes chat  (Kontext pro CallSid)
   └── TTS:   edge-tts → MP3  (de-AT-JonasNeural)
   │  TwiML <Play> + <Gather>
   ▼
Twilio spielt die Audio-Antwort ab → Anrufer hört Hermes
```

Der Voice-Server läuft **im selben Container** wie der Hermes-Agent und der
WhatsApp-Bot, als eigener Prozess auf Port 8765. Traefik läuft im `network_mode:
host` und erreicht den Server über die Bridge-IP des Hermes-Containers.

---

## Anruf-Ablauf

1. **Eingehender Anruf** — Twilio ruft `POST /voice/inbound` auf.
   Der Server begrüßt (TTS) und öffnet ein `<Gather input="speech">`.
2. Twilio transkribiert die Sprache und schickt den Text an `POST /voice/respond`.
3. Der Server übergibt den Text an Hermes, erzeugt aus der Antwort eine MP3 (TTS)
   und antwortet mit `<Play>` + erneutem `<Gather>` (nächste Gesprächsrunde).
4. Sagt der Anrufer „auf Wiederhören“ o. Ä., wird verabschiedet und aufgelegt.
5. Nach Anrufende räumt `POST /voice/status` die Session auf.

---

## Komponenten & Dateien

Alle Dateien liegen unter `/opt/hermes/voice/` (Eigentümer `hermes:hermes`):

| Datei | Zweck |
|-------|-------|
| `voice_server.py` | FastAPI-App mit den Voice-Endpoints |
| `config.py`       | Zentrale Konfiguration (liest `~/.hermes/.env`) |
| `stt.py`          | Speech-to-Text (Twilio-Gather; optional faster-whisper) |
| `tts.py`          | Text-to-Speech via edge-tts |
| `agent.py`        | Hermes-CLI-Aufruf mit Session-Kontext pro CallSid |
| `run.sh`          | Startskript (nutzt eigenes venv) |
| `.venv/`          | Eigenes venv (fastapi, uvicorn, twilio, edge-tts, httpx, python-multipart) |
| `requirements.txt`| Abhängigkeiten |
| `traefik/`        | Routing-Snippets (File-Provider + Docker-Labels) |

### HTTP-Endpoints

| Methode & Pfad | Zweck |
|----------------|-------|
| `POST /voice/inbound`   | Anrufeinstieg — bei Twilio konfiguriert |
| `POST /voice/respond`   | Empfängt Speech-Transkript, liefert Antwort-TwiML |
| `POST /voice/status`    | Call-Status-Callback (Session-Cleanup) |
| `GET  /voice/audio/{n}` | Liefert die TTS-MP3 an Twilio |
| `GET  /voice/health`    | Health-Check (ohne Auth) |

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
| `VOICE_PORT`             | `8765` | Interner Port |
| `VOICE_TTS_VOICE`        | `de-AT-JonasNeural` | edge-tts-Stimme |
| `VOICE_STT_BACKEND`      | `twilio` | `twilio` oder `whisper` |
| `VOICE_SPEECH_LANGUAGE`  | `de-DE` | Erkennungssprache |
| `VOICE_VALIDATE_SIGNATURE` | `1` | Signaturprüfung (für lokale Tests `0`) |
| `VOICE_AGENT_TIMEOUT`    | `12` | Sekunden für Hermes-Antwort (Twilio-Limit ~15s) |

---

## Deployment

### 1. Voice-Server (im Hermes-Container)

```bash
cd /opt/hermes/voice
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python \
  fastapi "uvicorn[standard]" twilio edge-tts httpx python-multipart
chown -R hermes:hermes /opt/hermes/voice
```

Twilio-Credentials in `~/.hermes/.env` ablegen (Eigentümer `hermes`, Modus `600`):

```
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+431XXXXXXX
```

### 2. Autostart

Der Container nutzt **kein s6-overlay als PID 1** (PID 1 ist `ttyd`). Der Autostart
erfolgt daher über `/entrypoint.sh`, analog zum Gateway-Start. Ergänzung nach dem
Gateway-Block:

```sh
# --- Hermes Voice Server (Twilio Voice Integration) ---
if [[ -x /opt/hermes/voice/.venv/bin/python ]] && ! pgrep -f "voice_server.py" >/dev/null 2>&1; then
  mkdir -p /opt/data/logs
  chown hermes:hermes /opt/data/logs
  gosu hermes env HOME=/opt/data/home \
    VOICE_PUBLIC_BASE_URL="https://srv1608402.hstgr.cloud" \
    nohup /opt/hermes/voice/.venv/bin/python /opt/hermes/voice/voice_server.py \
    >>/opt/data/logs/voice.log 2>&1 </dev/null &
fi
```

Der `pgrep`-Guard verhindert einen Doppelstart. Log: `/opt/data/logs/voice.log`.

> Hinweis: `/entrypoint.sh` liegt im Container-Dateisystem. Bei einem echten
> Image-Neubau muss der Eintrag erneut gesetzt werden.

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
# → {"ok":true,"tts_voice":"de-AT-JonasNeural","stt_backend":"twilio",...}

# Signaturprüfung aktiv? (ohne gültige Signatur muss 403 kommen)
curl -s -o /dev/null -w "%{http_code}\n" -X POST \
  https://srv1608402.hstgr.cloud/voice/inbound -d "CallSid=x"
# → 403

# Echter Testanruf
# +43 1 4351876 anrufen → Begrüßung hören → Frage stellen → Antwort hören
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

# Server manuell neu starten (wie der Entrypoint)
pkill -f voice_server.py
gosu hermes env HOME=/opt/data/home \
  VOICE_PUBLIC_BASE_URL="https://srv1608402.hstgr.cloud" \
  nohup /opt/hermes/voice/.venv/bin/python /opt/hermes/voice/voice_server.py \
  >>/opt/data/logs/voice.log 2>&1 &
```

---

## Troubleshooting

| Symptom | Ursache & Lösung |
|---------|------------------|
| Anruf gibt Fehlerton | Voice-Server läuft nicht — `pgrep -f voice_server.py`; Health prüfen. |
| `/voice/health` extern 404 | Traefik-File-Provider nicht aktiv oder `voice.yml`-Syntax falsch (Backticks in `rule:` prüfen). |
| Webhook liefert 403 | `VOICE_PUBLIC_BASE_URL` stimmt nicht exakt mit der Twilio-URL überein (Signatur schlägt fehl). |
| Hermes „dauert zu lange“ | Komplexe Anfrage > `VOICE_AGENT_TIMEOUT`. Timeout erhöhen oder einfachere Fragen. Twilio bricht Webhooks nach ~15s ab. |
| Keine Sprachausgabe | edge-tts braucht Internetzugang (Microsoft-Endpoint). |
| `hermes`-User kann `.env` nicht lesen | Eigentümer/Modus prüfen: `chown hermes:hermes ~/.hermes/.env && chmod 600`. |

---

## Sicherheit

- **Signaturvalidierung** (`X-Twilio-Signature`) ist standardmäßig aktiv. Requests
  ohne gültige Signatur werden mit `403` abgewiesen.
- Die Signatur wird gegen `VOICE_PUBLIC_BASE_URL` (die externe HTTPS-URL) berechnet,
  nicht gegen die interne HTTP-Adresse — daher muss `VOICE_PUBLIC_BASE_URL` exakt
  der Twilio-Webhook-URL entsprechen.
- Der Auth-Token liegt nur in `~/.hermes/.env` (Modus `600`), nicht im Code, nicht
  in der Doku, nicht im Memory.
- Der Server läuft als unprivilegierter `hermes`-User, niemals als root.

---

## Wartung & Grenzfälle

- **Container-Neustart:** unkritisch — der Voice-Server startet automatisch via
  `/entrypoint.sh` (mit pgrep-Guard gegen Doppelstart).
- **Hermes-Container-Neubau (Recreate):** Zwei Dinge können sich ändern und müssen
  ggf. nachgezogen werden:
  1. Die **Bridge-IP** `172.16.1.2` in `/docker/traefik/dynamic/voice.yml`
     (mit `docker inspect` prüfen).
  2. Der **`/entrypoint.sh`-Eintrag**, falls aus dem Image neu gebaut wird.
- **Traefik-Recreate:** immer mit `-p traefik` (korrekter Projektname), sonst wird
  ein falsches Projekt samt leerem acme-Volume erzeugt.
- **STT-Wechsel auf lokal:** `VOICE_STT_BACKEND=whisper` setzen und
  `uv pip install faster-whisper` im venv nachinstallieren.

---

*Erstellt 2026-07-17. Betrifft Host `srv1608402.hstgr.cloud`, Hermes-Container
`hermes-agent-7qpk`, Traefik-Projekt `traefik`.*
