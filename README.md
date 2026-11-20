# spot-history-manager

Lädt die eigene Foursquare-/Swarm-Check-in-Historie über die Foursquare API v2,
speichert sie in SQLite und exportiert JSON/CSV. Fotos werden als Bilddateien
mitgeladen, Kommentartexte separat nachgeladen.

Kein Cron, kein Hintergrunddienst: der Sync wird manuell aufgerufen, wenn neue
Check-ins vorhanden sind.

## Voraussetzungen

- Python 3.10+ (nur Standardbibliothek, keine pip-Abhängigkeiten)
- `.env` mit `FOURSQUARE_OAUTH_TOKEN` (OAuth-Token des eigenen Accounts)

```bash
cp .env.example .env
chmod 600 .env
```

```env
FOURSQUARE_OAUTH_TOKEN=...
```

Die Datei sollte `chmod 600` haben und wird nicht versioniert.

## Benutzung

```bash
# Erster Lauf: komplette Historie
python3 -m spot_history backfill

# Später: nur Neues seit dem letzten Stand
python3 -m spot_history sync

# Zusammenfassung
python3 -m spot_history stats

# Nur offene Fotos herunterladen
python3 -m spot_history photos

# Nur Exporte neu schreiben
python3 -m spot_history export
```

Optionen: `--data-dir`, `--db`, `--sleep`, `--no-photos`, `--no-export`, `--verbose`.

`sync` entscheidet selbst: ist noch kein Backfill erfolgt, wird die komplette
Historie geladen, sonst inkrementell ab dem gespeicherten Stand.

## Was gespeichert wird

```
data/
├── checkins.db      SQLite (Check-ins, Venues, Kategorien, Fotos, Kommentare, Likes, Sync-Status)
├── checkins.json    Rohdaten aller Check-ins (JSON-Array, aufsteigend nach Zeit)
├── checkins.csv     flache Tabelle
├── photos.csv       Fotometadaten + lokaler Pfad + Original-URL
├── comments.csv     Kommentartexte
├── likes.csv        Likes inkl. Nutzernamen
└── photos/
    └── <checkin_id>/<photo_id>.jpg
```

Pro Check-in werden u. a. gespeichert: `id`, `createdAt`, UTC- und lokale Zeit,
`timeZoneOffset`, `visibility`, `canonicalUrl`, `shout`, `isMayor`, Quelle/App,
Venue (Name, Adresse, Stadt, Land, Koordinaten, Zeitzone, Kategorie),
Kommentar-/Like-/Foto-Zähler, Likes samt Nutzern, Fotos (Metadaten + URL) sowie
das vollständige Roh-JSON in `checkins.raw_json`.

Kommentartexte liefert der Check-in-Endpoint nicht mit; sie werden für Check-ins
mit `comments.count > 0` über `/v2/checkins/<id>/comments` nachgeladen. Ob das
erfolgt ist, merkt sich `checkins.comments_synced`.

## Sync-Stand

`sync_state` merkt sich unter anderem:

- `last_max_created_at`: neuester verarbeiteter Check-in (Unix-Zeit)
- `last_sync_at`: Zeitpunkt des letzten Laufs
- `backfill_done`: ob die komplette Historie geladen wurde
- `api_total`: von der API gemeldete Gesamtzahl

Der inkrementelle Lauf holt die neuesten Seiten, bis er den gespeicherten Stand
erreicht, und schreibt neue/geänderte Check-ins per Upsert. `sync_runs` protokolliert
jeden Lauf (Seiten, neu, aktualisiert, Fehler).

## Technische Hinweise

- Paginierung beim Backfill erfolgt über `beforeTimestamp`, **nicht** über
  `offset`: Foursquare kappt Offset-Paging nach wenigen hundert Einträgen und
  wiederholt dann die erste Seite.
- `limit=250` pro Request, Standardpause 0,3 s, Retries mit Backoff bei 429/5xx.
- Bei `401` wird mit klarer Meldung abgebrochen; dann Token per OAuth-Flow
  erneuern und `.env` aktualisieren.
- Die API-Gesamtzahl kann minimal von der Zahl eindeutiger Check-ins abweichen
  (gelöschte/private Einträge); der Lauf warnt in diesem Fall.
- Fotos werden in Originalauflösung geladen. Das kann je nach Historie mehrere
  GiB belegen; `--no-photos` überspringt den Download.
- Das Roh-JSON enthält auch Namen von Nutzern, die Check-ins geliked haben.

## Tests

```bash
python3 -m unittest discover -s tests
```
