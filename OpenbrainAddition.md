# OpenBrain — Funktionalität, Vektor-Embeddings und geplante Erweiterungen

> Zusammenfassung einer Erklärungs-Session zu `HermesPlusOpenbrain` / `openbrain-mcp`.
> Quellen: `Openbrain.pdf` (Nate Jones, Substack), `Openbrain Github.url`
> (`github.com/NateBJones-Projects/OB1`), sowie der Code in diesem Repository.

## 1. Das Grundproblem, das "Open Brain" löst

Jede KI (Claude, ChatGPT, Cursor, …) startet bei jedem neuen Chat bei null.
Jeder Tool-Wechsel kostet Zeit fürs erneute Erklären von Kontext.
Plattform-eigene "Memory"-Features (Claude Memory, ChatGPT Memory) sind Silos,
die nicht miteinander reden. Kernthese: **Speicher-Architektur bestimmt die
Fähigkeiten eines Agenten mehr als die Modellwahl.**

## 2. Das Konzept: Open Brain

Eine selbst gehostete, semantische Wissensdatenbank, auf die *jede* KI über ein
offenes Protokoll zugreifen kann:

- **Postgres + pgvector** als Speicher (eine Tabelle, Vektor-Embeddings statt
  Keyword-Suche)
- **MCP (Model Context Protocol)** als universelle Schnittstelle — von
  Anthropic entwickelt, inzwischen auch von OpenAI/Google/Microsoft/Linux
  Foundation unterstützt
- **Capture-Kanal** (im Original: Slack) — man tippt einen Gedanken/Link
  rein, ein Service embedded und klassifiziert ihn automatisch (~5 Sekunden)
- **Retrieval** über MCP-Tools: semantische Suche, "letzte Einträge",
  Statistiken

Ergebnis: Man fragt "was hatte ich über X notiert" in völlig anderen Worten
als beim Speichern — und findet es trotzdem, weil nach *Bedeutung* gesucht
wird, nicht nach Text-Match.

## 3. Das GitHub-Repo OB1 — die Referenzimplementierung

`NateBJones-Projects/OB1` ist kein fertiges Produkt, sondern ein
**Lern- und Community-Baukasten**:

- **Getting-Started-Guide** (45-Minuten-Setup, Supabase-basiert, kein Code
  nötig)
- **6 Extensions** als Lernpfad (Haushalts-Wissensbasis → Wartungs-Tracker →
  Familienkalender → Essensplanung → CRM → Job-Pipeline), die aufeinander
  aufbauen
- **Primitives** (wiederverwendbare Bausteine: Edge Function deployen,
  Remote-MCP verbinden, Row-Level-Security, …)
- **Recipes** (Community-Beiträge: Import aus ChatGPT/Obsidian/Twitter/
  Gmail-Exports, Auto-Capture, Daily Digest, Deduplizierung, …)
- **Skills**, **Dashboards** (Next.js/SvelteKit-Frontends), **Integrations**
  (u. a. eine reine Kubernetes/Postgres-Variante *ohne* Supabase)

Der Standardpfad läuft über **Supabase** (gehostet) oder **Kubernetes** —
beides für einen kleinen VPS überdimensioniert.

## 4. Euer eigenes Projekt: HermesPlusOpenbrain

Dieses Repo ist genau dieser Gedanke, aber **bewusst schlank selbst gebaut**,
weil OB1s Standardpfade (Supabase/K8s) nicht auf einen 8-GB-VPS passen, auf
dem bereits der Hermes-Agent (WhatsApp-Bot) läuft. Statt Slack als
Capture-Kanal dient **WhatsApp**, statt Supabase Edge Functions ein
**schlanker Python-MCP-Dienst**.

**Architektur:**

- `openbrain-db`: Postgres 16 + pgvector, nur von `openbrain-mcp` erreichbar
  (Docker-Netzwerk-isoliert)
- `openbrain-mcp`: Python-Service mit lokalem Embedding-Modell
  (`intfloat/multilingual-e5-small`), Bearer-Token-Auth, hinter Traefik/TLS

**Die 10 bestehenden MCP-Tools**
(`openbrain-mcp/app/server.py`, delegiert an `openbrain-mcp/app/store.py`):

| Tool | Funktion |
|---|---|
| `save` | Notiz speichern, dedupliziert per Fingerprint (gleicher Link zweimal → kein Duplikat) |
| `search` | Semantische Suche, Top-k nach Ähnlichkeit |
| `list_recent` | Letzte Einträge |
| `stats` | Zählungen nach Quelle, Zeitspanne |
| `delete` | Fehlerhafte Einträge entfernen |
| `update` | Bearbeiten, re-embedded bei Summary-Änderung |
| `find_near_duplicates(threshold=0.95, limit=50)` | **Neu (2026-07-20).** Read-only: findet Notizpaare mit sehr ähnlicher Bedeutung per Embedding-Kosinus-Ähnlichkeit — ergänzt den exakten Fingerprint-Dedup aus `save` um Fälle mit unterschiedlicher Formulierung |
| `compute_fingerprint(raw_text, source_url?)` | **Neu (2026-07-21).** Read-only, kein DB-Zugriff: zeigt den SHA-256-Fingerprint, den `save` für diese Eingabe berechnen würde, plus die normalisierte Zeichenkette dahinter — zum Nachvollziehen/Debuggen des Fingerprint-Mechanismus, kein Duplikat-Check gegen bestehende Einträge |
| `cluster_captures(k?)` | **Neu (2026-07-21).** Read-only: gruppiert alle Notizen per k-Means nach Embedding-Ähnlichkeit in thematische Cluster — `k` optional, sonst automatisch per Silhouette-Score bestimmt. Gibt volle Cluster-Mitgliedschaft zurück, mit `central`-Flag für die (bis zu) 3 zentralsten Einträge je Cluster; die Themen-*Beschriftung* macht bewusst der aufrufende Client, nicht das Tool selbst |
| `classify_captures(categories, ids?)` | **Neu (2026-07-22).** Read-only: klassifiziert Notizen per Zero-Shot-Embedding-Ähnlichkeit in vom Aufrufer mitgegebene Kategorien (`{name, beispielsatz}`-Paare, nicht fix im Code) — keine Trainingsdaten nötig. Liefert pro Notiz die beste Kategorie plus Ähnlichkeits-Score; `ids` optional zum Eingrenzen. Speichert nichts selbst — Persistieren erfolgt über das bestehende `update`-Tool |

**Aktueller Stand** (laut `README.md`): System **komplett fertig und live**
(Phase 0–7 alle ✅) — auf dem VPS hinter Traefik mit echtem HTTPS deployt,
mit Hermes-Agent verbunden (WhatsApp end-to-end verifiziert), und auch von
Claude Desktop/Claude Code aus nutzbar (remote MCP-Server, gleiche
Datenbank).

## 5. Möglichkeiten (Ideen aus PDF + OB1-Repo, übertragbar)

- Weitere Clients anbinden (ChatGPT, Cursor, VS Code Copilot) — alle teilen
  sich dieselbe Datenbank
- Import bestehender Daten (Obsidian, ChatGPT-Export, E-Mail-Archiv) als
  Einmal-Migration
- Zusätzliche Capture-Quellen (Discord, direkter MCP-Write von jedem Client)
- Dashboards/Auswertungen über die Postgres-Daten
- Eigene Metadaten-Schemas für spezielle Zwecke (CRM, Meeting-Notizen, …)
- Sicherheits-Härtung (im Repo als offener Punkt vermerkt): separates
  Read-Token für Laptop-Clients vs. Write-Token für Hermes,
  konstante-Zeit-Tokenvergleich

## 6. Vektor-Embeddings — wie sie funktionieren

Ein Embedding-Modell wandelt Text in einen Vektor aus Zahlen um (hier: 384
Dimensionen), der die *Bedeutung* des Texts codiert. Zwei Texte mit
ähnlicher Bedeutung liegen im Vektorraum nahe beieinander — unabhängig
davon, ob sie dieselben Wörter benutzen. "Ähnlichkeit" misst man als Distanz
zwischen den Vektoren (hier: Kosinus-Distanz).

### Wie es im Code verwendet wird

**Beim Speichern** (`openbrain-mcp/app/store.py:24`):
```python
emb = embed_passage(summary)   # Text -> 384-dim Vektor
# ... wird zusammen mit dem Text in Postgres gespeichert
```

**Beim Suchen** (`openbrain-mcp/app/store.py:48`):
```sql
SELECT id, summary, ..., 1 - (embedding <=> %s::vector) AS score
FROM captures
ORDER BY embedding <=> %s::vector
LIMIT %s
```
`<=>` ist der pgvector-Operator für Kosinus-Distanz. Die Anfrage wird selbst
zuerst embedded (`embed_query`), dann sucht Postgres die nächsten Nachbarn im
Vektorraum.

**Der HNSW-Index** (`openbrain-mcp/migrations/001_init.sql:20`) macht das
auch bei vielen Einträgen schnell — statt jeden Vektor einzeln zu
vergleichen (linear wachsend), navigiert HNSW näherungsweise zu den
nächsten Nachbarn.

**Wichtiges Detail — asymmetrisches e5-Modell**
(`openbrain-mcp/app/embeddings.py:10-18`):
```python
def embed_passage(text): ...  # Präfix "passage: " — für gespeicherte Dokumente
def embed_query(text): ...    # Präfix "query: "   — für Suchanfragen
```
Das `intfloat/multilingual-e5-small`-Modell wurde so trainiert, dass
Dokument und Anfrage unterschiedliche Präfixe brauchen, um im selben
Bedeutungsraum gut zu matchen. Vergisst man das, verschlechtert sich die
Suchqualität spürbar — ein häufiger Anfängerfehler bei e5-Modellen.

### Wie man Embeddings selbst nutzen/abfragen kann

**A) Über das MCP-Tool `search`** — von Claude Code/Desktop oder
WhatsApp/Hermes aus: *"durchsuche mein Brain nach …"*.

**B) Direkt per SQL** (z. B. via `psql`):
```sql
SELECT summary, 1 - (embedding <=> (SELECT embedding FROM captures WHERE id = '<uuid>')) AS score
FROM captures
ORDER BY embedding <=> (SELECT embedding FROM captures WHERE id = '<uuid>')
LIMIT 5;
```

**C) Eigenes Python-Script** (Bausteine wiederverwenden):
```python
from app.embeddings import embed_query
from app.db import get_conn

vec = embed_query("Was war das nochmal mit Sarahs Jobwechsel?")
with get_conn() as conn, conn.cursor() as cur:
    cur.execute(
        "SELECT summary, 1 - (embedding <=> %s::vector) AS score "
        "FROM captures ORDER BY embedding <=> %s::vector LIMIT 5",
        (vec, vec),
    )
    for summary, score in cur.fetchall():
        print(f"{score:.3f}  {summary}")
```

### Weitere Anwendungsfälle für Embeddings (allgemein)

- **Duplikaterkennung**: sehr ähnliche Vektoren (Kosinus-Distanz nahe 0)
  sind oft inhaltliche Duplikate — ergänzend zum bestehenden
  `fingerprint`-Mechanismus, der nur exakte/URL-Duplikate fängt
- **Clustering**: Einträge nach Themen gruppieren, ohne dass man Kategorien
  vorher definiert
- **Klassifikation**: neue Texte dem nächstgelegenen bekannten Beispiel
  zuordnen
- **Empfehlungen**: "ähnliche Einträge zu diesem hier" anzeigen
- **RAG (Retrieval-Augmented Generation)**: relevante Textstücke per
  Embedding-Suche finden und einem LLM als Kontext mitgeben — genau das
  Muster, das dieses Projekt bereits umsetzt

## 7. Beispiel-Prompts

### Fingerprint-Dedup (existiert bereits, läuft automatisch)

Kein Prompt nötig — passiert bei jedem `save`. Beispiel: derselbe
YouTube-Link zweimal per WhatsApp (unterschiedliche `?si=`-Parameter):

```
Du:     https://youtu.be/abc123?si=xyz111
Hermes: Gespeichert: "..." (Quelle: youtube)

Du:     https://youtu.be/abc123?si=xyz999   ← anderer Tracking-Parameter
Hermes: Das hatte ich schon — gleicher Inhalt, bereits gespeichert.
```
Technisch: `content_fingerprint()` normalisiert die URL (strippt `si=`,
`www.` etc.) zu einem SHA-256-Hash **vor** dem Embedding — exakte
URL-Duplikate werden also gar nicht erst eingebettet.

### Semantische Suche (existiert bereits — `search`)

```
"Durchsuche mein Brain nach dem, was ich über Sarahs Jobwechsel notiert hatte."
"Was hab ich zum Thema Steuervorteile bei MU gespeichert?"
"Finde die Notiz zu dem Substack-Artikel über Prompt Engineering."
```

### Duplikatserkennung per Embedding-Ähnlichkeit (existiert bereits — `find_near_duplicates`)

Fingerprint fängt nur *exakte* Duplikate. Zwei Notizen mit demselben Inhalt
in leicht anderen Worten rutschen durch — dafür gibt es jetzt ein eigenes,
read-only Tool. Endnutzer-Prompt, wörtlich so an Claude Code/Desktop:

```
"Nutze openbrains find_near_duplicates-Tool und sag mir, welche Notizen
sich inhaltlich überschneiden."
"Finde Notizpaare mit mindestens 90% Ähnlichkeit in meinem Brain."
```
Beispielergebnis (real gegen die Testdatenbank verifiziert): zwei Notizen
"Sarah is considering leaving her job to start a consulting **business**."
und "...to start a consulting **company**." wurden mit Similarity `0.997`
als Paar gefunden — das Tool selbst mutiert nichts, Aufräumen erfolgt
weiterhin über das bestehende `delete`-Tool.

Umsetzung (`openbrain-mcp/app/store.py`):
```sql
SELECT a.id, a.summary, b.id, b.summary,
       1 - (a.embedding <=> b.embedding) AS similarity
FROM captures a JOIN captures b ON a.id < b.id
WHERE 1 - (a.embedding <=> b.embedding) > %s   -- Standard-Threshold: 0.95
ORDER BY similarity DESC LIMIT %s;             -- Standard-Limit: 50
```

### Expliziter Fingerprint-Check (existiert bereits — `compute_fingerprint`)

`save` dedupliziert automatisch und lautlos per Fingerprint — man sieht nie,
*welcher* Hash oder *welche* normalisierte Zeichenkette dahintersteckt. Für
Debugging/Nachvollziehbarkeit gibt es jetzt ein separates, rein rechnendes
Tool ganz ohne DB-Zugriff. Endnutzer-Prompt, wörtlich so an Claude Code/
Desktop:

```
"Nutze openbrains compute_fingerprint-Tool und zeig mir, welchen Fingerprint
https://www.youtube.com/watch?v=abc123 ergibt."
"Warum werden diese zwei URLs als dasselbe erkannt? Berechne beide
Fingerprints und vergleiche die normalisierte Basis."
```
Beispielergebnis (real gegen den lokalen Compose-Stack verifiziert):
`compute_fingerprint(source_url="https://www.Example.com/Page/")` liefert
`{"fingerprint": "d641f3ec...", "normalized_basis": "example.com/page",
"basis_source": "url"}` — man sieht direkt, dass `www.`, Groß-/
Kleinschreibung und der abschließende Slash vor dem Hashen entfernt wurden.
Ohne `source_url` fällt das Tool auf normalisierten Text zurück
(`basis_source: "text"`).

Umsetzung (`openbrain-mcp/app/fingerprint.py`):
```python
def content_fingerprint_debug(*, source_url: str | None, raw_text: str) -> dict:
    basis, source = _compute_basis(source_url=source_url, raw_text=raw_text)
    return {
        "fingerprint": hashlib.sha256(basis.encode("utf-8")).hexdigest(),
        "normalized_basis": basis,
        "basis_source": source,
    }
```
Bewusst **kein** Duplikat-Check gegen die Datenbank — dafür gibt es bereits
`save` (automatisch) und `find_near_duplicates` (Embedding-basiert). Dieses
Tool beantwortet nur "welcher Hash, und warum", nicht "gibt's das schon".

### Clustering (existiert bereits — `cluster_captures`)

Themen automatisch gruppieren, ohne sie vorher zu definieren. Das Tool selbst
macht keine LLM-Aufrufe und beschriftet nichts — es liefert nur die
Rohdaten (Cluster-Mitgliedschaft + die zentralsten Einträge je Cluster);
die Themen-Beschriftung übernimmt der aufrufende Client (Claude Code/
Desktop, Hermes), da der ja selbst ein LLM ist. Endnutzer-Prompt:

```
"Nutze openbrains cluster_captures-Tool und beschrifte mir die Themen
pro Cluster anhand der zentralen Einträge."
"Clustere meine Notizen mit k=5 und zeig mir, was in jedem Cluster ist."
"Ich hab das Gefühl, ich notiere immer wieder dieselben paar Dinge —
cluster automatisch (ohne k) und sag mir, ob das stimmt."
"Gib mir eine grobe Themenübersicht über mein ganzes Brain mit k=10,
jeweils mit Titel und Anzahl der Notizen pro Cluster."
```
Beispielergebnis (real gegen den lokalen Compose-Stack verifiziert): 6
Notizen — 3 zu "Sarah plant Jobwechsel in Richtung Consulting" (business/
company/firm), 3 zu einem Sauerteig-Brotrezept (rye/wheat/spelt starter) —
wurden sowohl mit `k=2` als auch mit automatischer k-Bestimmung
(Silhouette-Score, gewählt: `k=2`) sauber in zwei themenreine Cluster
getrennt, alle 3 Mitglieder je Cluster als `central: true` markiert (bei
Clustergröße 3 sind alle zentral). `stats` vor/nach bestätigt: das Tool
mutiert nichts.

Umsetzung (`openbrain-mcp/app/store.py`):
```python
def cluster_captures(conn: psycopg.Connection, *, k: int | None = None) -> dict:
    ...
    if k is None:
        k = _auto_select_k(embeddings)  # sklearn KMeans + silhouette_score, k=2..10
    model = KMeans(n_clusters=k, random_state=0, n_init=10).fit(embeddings)
    distances = model.transform(embeddings)  # Distanz jedes Punkts zu jedem Centroid
    ...
```
Ungültiges `k` (0, negativ, oder größer als die Gesamtzahl an Notizen) und
zu wenige Notizen insgesamt (< 4) liefern beide ein sauberes
`{"error": "..."}`-Dict statt eines Absturzes.

### Klassifikation (existiert bereits — `classify_captures`)

Zero-Shot: keine Trainingsdaten nötig, das Tool vergleicht per
Embedding-Ähnlichkeit gegen einen Beispielsatz je Kategorie. Anders als
beim ursprünglichen Sketch sind die Kategorien **nicht** fix im Code
hinterlegt, sondern werden bei jedem Aufruf vom Client mitgegeben —
konsistent mit `cluster_captures`s Philosophie: das Tool macht die
Embedding-Mathematik, der Client (selbst ein LLM) bringt die inhaltliche
Definition mit. Das Tool selbst ist read-only und speichert nichts —
Persistieren erfolgt bewusst getrennt über das bestehende `update`-Tool.
Endnutzer-Prompts:

```
"Klassifiziere alle meine Notizen in die Kategorien Entscheidung,
Personen-Notiz, Insight, Meeting-Debrief, sonstiges — mit je einem
Beispielsatz pro Kategorie — und fasse zusammen, wie viele in welche
Kategorie fallen."
"Nutze classify_captures und sag mir, ob diese eine bestimmte Notiz eher
'Karriere' oder 'Freizeit' ist." (mit ids auf die eine Notiz eingegrenzt)
"Klassifiziere meine Notizen in Themen, die zu meinem Job passen, und
speichere das Ergebnis für jede als category in ihren metadata."
```
Beispielergebnis (real gegen den lokalen Compose-Stack verifiziert): 4
Notizen — 2 zu "Sarah plant Jobwechsel" (business/company), 2 zu einem
Sauerteig-Brotrezept (rye/wheat starter) — wurden mit den Kategorien
`career`/`cooking` (je ein Beispielsatz) korrekt zugeordnet, Similarity-
Scores zwischen `0.84` und `0.85`. Der `ids`-Filter auf nur die 2
"career"-Notizen lieferte exakt diese beiden zurück; eine leere
Kategorien-Liste lieferte ein sauberes Error-Dict statt eines Crashs.
`stats` vor/nach bestätigt: das Tool mutiert nichts.

Umsetzung (`openbrain-mcp/app/store.py`):
```python
def classify_captures(conn, *, categories: list[dict], ids=None):
    # categories: [{"name": "career", "example": "..."}, ...]
    category_embeddings = [embed_passage(c["example"]) for c in categories]
    sims = cosine_similarity(capture_embeddings, category_embeddings)
    # pro Notiz: Kategorie mit dem höchsten Score (Argmax)
```
Zwei-Schritt-Workflow zum dauerhaften Speichern: erst `classify_captures`
aufrufen, dann pro Notiz `update(id, metadata={"category": "..."})` —
Achtung, `metadata` wird dabei komplett ersetzt, nicht gemergt. Ein
späteres gezieltes Filtern nach `category` (z. B. "zeig mir alle
Entscheidung-Notizen") ist bewusst **nicht** Teil dieser Fähigkeit — dafür
müsste `metadata` erst lesbar gemacht werden (aktuell "write-only", siehe
`README.md`), das wäre eine eigene, spätere Erweiterung.

## 8. Erweiterungen — Status

Vier Fähigkeiten, die als neue MCP-Tools nach dem bestehenden Muster
(`store.py` + `server.py` + Tests) in `openbrain-mcp` eingebaut werden:

1. ✅ **Embedding-basierte Duplikatserkennung** (`find_near_duplicates`) —
   fertig, gemergt auf `main` am 2026-07-20. Spec:
   [`docs/superpowers/specs/2026-07-20-openbrain-duplicate-detection-design.md`](docs/superpowers/specs/2026-07-20-openbrain-duplicate-detection-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-20-openbrain-duplicate-detection.md`](docs/superpowers/plans/2026-07-20-openbrain-duplicate-detection.md).
   21/21 Tests grün, End-to-End-Smoke-Test gegen einen echten lokalen
   Docker-Compose-Stack verifiziert.
2. ✅ **Expliziter Fingerprint-Check** (`compute_fingerprint`) — fertig,
   gemergt auf `main` am 2026-07-21. Spec:
   [`docs/superpowers/specs/2026-07-21-openbrain-fingerprint-debug-design.md`](docs/superpowers/specs/2026-07-21-openbrain-fingerprint-debug-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-21-openbrain-fingerprint-debug.md`](docs/superpowers/plans/2026-07-21-openbrain-fingerprint-debug.md).
   24/24 Tests grün (die 3 neuen Tests brauchen keine Datenbank), End-to-End-
   Smoke-Test gegen einen echten lokalen Docker-Compose-Stack verifiziert.
   Bewusste Abweichung vom `server.py`→`store.py`-Muster der anderen sieben
   Tools: `compute_fingerprint` ruft `content_fingerprint_debug` direkt auf,
   da kein DB-Zugriff nötig ist.
3. ✅ **Clustering** (`cluster_captures`) — fertig, gemergt auf `main` am
   2026-07-21. Spec:
   [`docs/superpowers/specs/2026-07-21-openbrain-clustering-design.md`](docs/superpowers/specs/2026-07-21-openbrain-clustering-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-21-openbrain-clustering.md`](docs/superpowers/plans/2026-07-21-openbrain-clustering.md).
   29/29 Tests grün, End-to-End-Smoke-Test gegen einen echten lokalen
   Docker-Compose-Stack verifiziert (6 Notizen aus 2 Themen sauber getrennt,
   sowohl mit festem `k` als auch automatisch per Silhouette-Score). Neue
   Abhängigkeit: `scikit-learn`. Keine LLM-Aufrufe in `openbrain-mcp` — die
   Themen-Beschriftung macht der aufrufende Client. Review-getriebene
   Ergänzung: ungültiges `k` liefert ein sauberes Error-Dict statt eines
   sklearn-Stacktraces.
4. ✅ **Klassifikation** (`classify_captures`) — fertig, gemergt auf `main`
   am 2026-07-22. Spec:
   [`docs/superpowers/specs/2026-07-22-openbrain-classification-design.md`](docs/superpowers/specs/2026-07-22-openbrain-classification-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-22-openbrain-classification.md`](docs/superpowers/plans/2026-07-22-openbrain-classification.md).
   33/33 Tests grün, End-to-End-Smoke-Test gegen einen echten lokalen
   Docker-Compose-Stack verifiziert (4 Notizen aus 2 Themen korrekt
   klassifiziert, `ids`-Filter und Error-Fall verifiziert). Keine neue
   Abhängigkeit (nutzt `scikit-learn`, bereits durch Clustering vorhanden).
   Zero-Shot mit **vom Aufrufer mitgegebenen** Kategorien (nicht fix im
   Code) — read-only, Persistieren bewusst getrennt über das bestehende
   `update`-Tool. `metadata`-Rücklesen/-Filtern explizit **nicht** Teil
   dieser Fähigkeit (siehe §7).

5. ✅ **Web-GUI Phase 1** (`openbrain-gui`) — fertig implementiert und
   gemergt auf `main` am 2026-07-24. Spec:
   [`docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md`](docs/superpowers/specs/2026-07-24-openbrain-gui-phase1-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md`](docs/superpowers/plans/2026-07-24-openbrain-gui-phase1.md).
   22/22 neue Backend-Tests grün (`openbrain-gui/backend/tests/`), plus das
   neue `list_keywords`-Tool in `openbrain-mcp` (36/36 Tests dort grün
   insgesamt). React-Frontend (Vite) + FastAPI-Backend in einem Container,
   per Multi-Stage-Dockerfile gebaut — lokal per Docker-Build und
   Live-Browser-Test Ende-zu-Ende verifiziert (Suche, Ändern, Löschen,
   Lösch-Log, gespeicherte Prompts, Theme-Umschaltung). Zugriff auf
   `openbrain-db` ausschließlich über `openbrain-mcp` (wie Claude
   Desktop/Code) — kein direkter Postgres-Zugriff. Eigene kleine
   SQLite-Datenbank (`gui.db`) für gespeicherte Prompts und das Lösch-Log,
   getrennt von `openbrain-db`. Subject-Lines pro Ergebniszeile werden per
   einfacher Kürzung der ersten 10 Wörter der bereits vorhandenen Summary
   erzeugt — kein eigener LLM-Call, da die Summary schon zum Capture-
   Zeitpunkt von Hermes prägnant formuliert wird (ursprünglich war dafür ein
   Live-Aufruf von Claude Haiku pro Zeile vorgesehen; nach Praxistest als
   unnötiger Overhead erkannt und entfernt). Einzelbenutzer-Zugriff über
   Traefik Basic-Auth, kein Login-Screen — bewusst akzeptierter Kompromiss
   ohne eigene App-Level-Authentifizierung (anders als `openbrain-mcp`), da
   `openbrain-gui` nur auf demselben privaten VPS-Netz läuft.
   **Live seit 2026-07-25** unter `https://gui.<vps-host>.hstgr.cloud`,
   Compose-Service + Traefik-Labels + Env-Vars (`OPENBRAIN_GUI_HOST`,
   `GUI_BASIC_AUTH_USERS`) in `deploy/docker-compose.openbrain.yml` bzw.
   `deploy/.env.example` committet. Nach dem Go-Live noch auf
   Praxis-Feedback angepasst: Datums-Anzeige lesbarer formatiert, Change-
   Popup vergrößert, neuer Read-only "Summary"-Button/Popup, Relevanz-Score
   pro Ergebnis sichtbar (statt eines blind geratenen Cutoffs — Kalibrierung
   zeigte, dass ein fester Schwellwert bei diesem thematisch engen Corpus
   keine sauberen Treffer/Nicht-Treffer trennt), Quell-URLs als klickbare
   Links. Phase 2 (Wordcloud, AND/OR-Keyword-Suche) wurde übersprungen —
   siehe Punkt 6.

6. ✅ **Web-GUI Phase 3 — Keyword-Graph** (`openbrain-gui`) — eine neue
   "Show keyword graph"-Ansicht (neben "Show delete log") zeigt jedes
   Keyword als Bubble, Größe = Häufigkeit, Farbe = automatisch erkanntes
   Themen-Cluster — komplett über das bestehende `cluster_captures`-Tool,
   keine neue `openbrain-mcp`-Fähigkeit nötig. Hover auf einer Bubble oder
   einer Cluster-Zeile in der Legende zeigt die zugehörigen Einträge
   (zentrale/repräsentativste mit ★ markiert); Klick auf eine Bubble fügt
   das Keyword ins Suchfeld ein, wie bei der bestehenden Keyword-Liste.
   Zoom/Pan per Scrollrad, Ziehen oder +/−-Buttons (`d3-force` fürs
   Cluster-Layout, `d3-zoom` fürs Zoomen — zwei kleine, gezielte
   Zusatzpakete). Phase 2 (Wordcloud, AND/OR-Keyword-Suche) wurde bewusst
   übersprungen. Spec:
   [`docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md`](docs/superpowers/specs/2026-07-25-openbrain-gui-phase3-keyword-graph-design.md),
   Plan:
   [`docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md`](docs/superpowers/plans/2026-07-25-openbrain-gui-phase3-keyword-graph.md).

Damit sind alle 4 ursprünglich geplanten MCP-Fähigkeiten sowie Phase 1 und
Phase 3 der Web-GUI umgesetzt und deployed (Phase 2 bewusst übersprungen).
Details zu jeder einzelnen siehe die jeweiligen Spec-/Plan-Dokumente unter
`docs/superpowers/`.
