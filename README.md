# spot-history-manager

Sammelt Check-in-/Besuchshistorien aus mehreren Diensten, speichert jede Quelle
in einer eigenen SQLite-Datenbank und baut daraus einen zentralen, rebuildbaren
Index plus GeoJSON für eine spätere OpenStreetMap-Karte.

Aktuell implementiert: **Foursquare/Swarm** (API-Sync) und **Yelp** (einmaliger HTML-Import).
Geplant: **Google Timeline** (Import).

## Voraussetzungen

- Python 3.10+; Sync, Index und Importe nutzen nur die Standardbibliothek
- Für die Web-Oberfläche: FastAPI, uvicorn und Jinja2 aus `requirements.txt`
- `.env` mit `FOURSQUARE_OAUTH_TOKEN`

```bash
cp .env.example .env
chmod 600 .env
```

```env
FOURSQUARE_OAUTH_TOKEN=...
```

## Benutzung

```bash
# Foursquare: erster Lauf (komplette Historie)
python3 -m spot_history foursquare backfill

# Später: nur Neues seit dem letzten Stand
./sync.sh
# oder:
python3 -m spot_history foursquare sync

# Zusammenfassung
python3 -m spot_history foursquare stats

# Alle Quellen in den zentralen Index + GeoJSON überführen
python3 -m spot_history index build

# Web-Oberfläche (Liste + OpenStreetMap-Karte)
.venv/bin/pip install -r requirements.txt   # einmalig
./web.sh                                    # 0.0.0.0:8345
```

Optionen beim Foursquare-Connector: `--data-dir`, `--db`, `--sleep`,
`--no-photos`, `--no-export`, `--verbose`. Die Web-Oberfläche liest nur den
Index; `index build` läuft getrennt davor.

## Datenlayout

```
data/
├── foursquare/
│   ├── foursquare.db          Quelle (Check-ins, Venues, Fotos, Kommentare, Likes, Sync-Status)
│   ├── exports/               checkins.json/csv, photos.csv, comments.csv, likes.csv
│   └── photos/<checkin_id>/   Bilddateien
├── yelp/                      yelp.db + media/ (HTML-Import)
├── google/                    (kommt später) google.db
├── index/index.db             zentraler, rebuildbarer Index
└── exports/visits.geojson     Kartenpunkte aller Quellen
```

Die Quell-DBs sind die Wahrheit. Der Index wird bei jedem `index build` neu aus
den Connectors erzeugt und kann gefahrlos gelöscht werden.

## Web-Oberfläche

`./web.sh` startet FastAPI/Jinja auf `http://<host>:8345` (Bind `0.0.0.0`).
Die Seite zeigt standardmäßig alle Besuche in München, mit Filtern für Stadt,
Jahr, Kategorie, Quelle und Freitext. Oben eine Leaflet-Karte mit
OpenStreetMap-Tiles (Leaflet liegt lokal unter
`spot_history/web/static/vendor/leaflet/`), darunter die Liste mit 60 Treffern
pro Seite. Klick auf Karte und Liste ist synchronisiert.

Die Oberfläche hat **keine Authentifizierung** und ist für LAN/interne Nutzung
gedacht. Fotos werden noch nicht als Thumbnails angezeigt, nur ihre Anzahl und
der Link zum Original.

## Zentraler Index

- `sources` – welche Quellen enthalten sind
- `places` – Orte je Quelle (`source`, `source_place_id`)
- `visits` – Besuche/Check-ins mit Zeit, Koordinaten, Notiz, Quell-URL
- `photos` – Fotometadaten + lokaler Pfad
- `index_meta` – Build-Zeitpunkt

`visits.geojson` ist eine `FeatureCollection` mit einem Punkt pro Visit
(Koordinaten als `[lng, lat]`), inklusive Quelle, Ort, Kategorie, Zeit und
lokalen Fotopfaden. Das reicht für Leaflet/MapLibre mit OSM-Tiles.

## Foursquare-Details

- Check-ins: 2339 laut API, 2338 eindeutig (1 gelöscht/privat)
- 1036 Venues, 577 Fotos (562 mit Check-in verknüpft, 15 standalone),
  115 Kommentare, 294 Likes
- Zeitraum 2010-01-28 bis 2026-09-01
- Paginierung über `beforeTimestamp` (Offset-Paging wird von Foursquare nach
  wenigen hundert Einträgen gekappt und wiederholt dann die erste Seite)
- Kommentartexte werden über `/v2/checkins/<id>/comments` nachgeladen
- Der Sync-Stand (`last_max_created_at`) liegt in `sync_state`; `sync` holt nur
  Neues und schreibt Exporte neu
- Zusätzlich importiert: `venuehistory` (1029 von 1051 API-Venues), `lists`
  (5 Listen, ~150 Listeneinträge; bei „My Liked Places“ 53 von 54), `tips`
  (11) und alle Nutzerfotos (577; 562 mit Check-in, 15 standalone)

## Yelp-Import

```bash
python3 -m spot_history yelp import
python3 -m spot_history index build       # Foursquare-Index für Namensmatches
python3 -m spot_history yelp geocode      # Nominatim, max. 1 Request/s
python3 -m spot_history index build       # Städte in den Index übernehmen
python3 -m spot_history yelp stats
```

- 462 Check-ins, 284 Orte, 92 mit Quellkoordinaten, 270 Medien
- 135 Yelp-Kartenpunkte: 92 mit Quellkoordinaten, 43 über den Ort abgeleitet
  (Badge „Ort abgeleitet“ in Liste und Karte)
- 316 von 462 Visits mit Stadt: 135 über Nominatim, 181 über exakte
  Namensmatches gegen Foursquare-Index und Export-URLs
- 146 Visits bleiben ohne Stadt, 327 ohne Kartenpunkt
- Der Geocoding-Cache liegt unter `data/yelp/` und ist gitignored;
  `--force` erzwingt neue Nominatim-Requests

## Weitere Quellen

Der Connector-Vertrag steht in [`CONNECTORS.md`](CONNECTORS.md). Connectoren
werden automatisch unter `spot_history/connectors/*` entdeckt und müssen dort
`CONNECTOR` exportieren. Der Yelp-Import ist als Agenten-Prompt vorbereitet:
[`prompts/yelp-import.md`](prompts/yelp-import.md). Die Foursquare-Erweiterung:
[`prompts/foursquare-extras.md`](prompts/foursquare-extras.md).

## Tests

```bash
python3 -m unittest discover -s tests
```

## Hinweise

- `.env` und `data/` sind gitignored und gehören nicht ins Repository.
- Das Roh-JSON enthält auch Namen von Nutzern, die Check-ins geliked haben.
- Fotos werden in Originalauflösung geladen; `--no-photos` überspringt das.
- Kein Cron/Daemon: Sync und Index werden manuell aufgerufen.
