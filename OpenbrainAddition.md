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

**Die 6 bestehenden MCP-Tools**
(`openbrain-mcp/app/server.py`, delegiert an `openbrain-mcp/app/store.py`):

| Tool | Funktion |
|---|---|
| `save` | Notiz speichern, dedupliziert per Fingerprint (gleicher Link zweimal → kein Duplikat) |
| `search` | Semantische Suche, Top-k nach Ähnlichkeit |
| `list_recent` | Letzte Einträge |
| `stats` | Zählungen nach Quelle, Zeitspanne |
| `delete` | Fehlerhafte Einträge entfernen |
| `update` | Bearbeiten, re-embedded bei Summary-Änderung |

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

### Duplikatserkennung per Embedding-Ähnlichkeit (noch zu bauen)

Fingerprint fängt nur *exakte* Duplikate. Zwei Notizen mit demselben Inhalt
in leicht anderen Worten rutschen durch. Prompt an Claude Code:

```
"Schreib ein Script, das in der captures-Tabelle Paare mit Kosinus-Ähnlichkeit
über 0.95 findet (embedding <=> embedding), sie mir mit Summary und Score
auflistet, und mich vor jedem Löschen fragt, welchen der beiden ich behalten
will."
```
Umsetzung sinngemäß:
```sql
SELECT a.id, a.summary, b.id, b.summary,
       1 - (a.embedding <=> b.embedding) AS similarity
FROM captures a JOIN captures b ON a.id < b.id
WHERE 1 - (a.embedding <=> b.embedding) > 0.95
ORDER BY similarity DESC;
```

### Clustering (noch zu bauen)

Themen automatisch gruppieren, ohne sie vorher zu definieren:

```
"Lies alle embeddings + summaries aus captures, clustere sie mit k-means
(k=8, oder automatisch per Silhouette-Score bestimmt), und gib mir pro
Cluster eine kurze Themen-Beschriftung plus die 3 zentralsten Einträge."
```
Umsetzung: Embeddings via `psycopg` laden → `sklearn.cluster.KMeans` (oder
`hdbscan` für automatische Clusterzahl) → pro Cluster die Summaries an ein
LLM zur Beschriftung geben.

### Klassifikation (noch zu bauen)

**a) Zero-Shot** (keine Trainingsdaten nötig, nutzt Ähnlichkeit zu
Label-Beschreibungen):
```
"Klassifiziere jede Notiz aus den letzten 30 Tagen in eine dieser Kategorien:
Entscheidung, Personen-Notiz, Insight, Meeting-Debrief, sonstiges — indem du
die Ähnlichkeit des Embeddings zu einem Beispielsatz je Kategorie berechnest,
und speichere das Ergebnis ins metadata-Feld als {"category": "..."}."
```

**b) Neuer Eintrag zur nächstgelegenen bekannten Kategorie**:
```
"Wenn eine neue Notiz reinkommt: finde die 5 ähnlichsten bereits
kategorisierten Einträge (per Embedding-Distanz) und übernimm die
Mehrheits-Kategorie."
```

> Hinweis: Das `metadata jsonb`-Feld ist laut `README.md` aktuell
> "write-only" (wird gespeichert, aber von keinem Tool zurückgelesen) — für
> Variante (a) braucht es zusätzlich ein `get-by-id`- oder ein erweitertes
> `search`-Tool, das `metadata` mit ausliest.

## 8. Geplante Erweiterungen (nächster Schritt)

Vier Fähigkeiten, die als neue MCP-Tools nach dem bestehenden Muster
(`store.py` + `server.py` + Tests) in `openbrain-mcp` eingebaut werden
sollen:

1. Embedding-basierte Duplikatserkennung (über den exakten Fingerprint
   hinaus)
2. Expliziter Fingerprint-/Duplikat-Check als eigenständiges Tool
3. Clustering
4. Klassifikation (inkl. Rücklesen von `metadata`)

Details und Fortschritt siehe die jeweiligen Spec-/Plan-Dokumente unter
`docs/superpowers/`.
