# spot-history-manager

Sammelt Check-in-/Besuchshistorien aus mehreren Diensten, speichert jede Quelle
in einer eigenen SQLite-Datenbank und baut daraus einen zentralen, rebuildbaren
Index plus GeoJSON für eine spätere OpenStreetMap-Karte.

Aktuell implementiert: **Foursquare/Swarm** (API-Sync), **Yelp**
(einmaliger HTML-Import) und **Google Timeline** (iPhone-/Takeout-Import mit
kostenloser OSM-Anreicherung).

## Voraussetzungen

- Python 3.10+; Sync, Index und Importe nutzen nur die Standardbibliothek
- Für die Web-Oberfläche: FastAPI, uvicorn, Jinja2 und Pillow aus `requirements.txt`
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

# API-Endpunkte nur lesend prüfen (v2 + Places API)
python3 -m spot_history foursquare probe

# Places-API-Baseline einmalig aus JSON importieren (ohne API-Calls)
python3 -m spot_history foursquare venue-import

# Google Timeline exportieren, prüfen, importieren und über OSM anreichern
python3 -m spot_history google scan     --input "$GOOGLE_TIMELINE_INBOX"
python3 -m spot_history google import   --input "$GOOGLE_TIMELINE_INBOX"
python3 -m spot_history google osm
python3 -m spot_history google stats

# Alle Quellen in den zentralen Index + GeoJSON überführen
python3 -m spot_history index build

# Kanonische Orte prüfen und manuell korrigieren
python3 -m spot_history places stats
python3 -m spot_history places candidates
python3 -m spot_history places merge yelp:<place_key> foursquare:<venue_id>

# Thumbnails für die Web-UI (Pillow, einmalig bzw. nach neuen Fotos)
.venv/bin/python -m spot_history index thumbs

# Web-Oberfläche (Liste + OpenStreetMap-Karte + Fotos)
.venv/bin/pip install -r requirements.txt   # einmalig
./web.sh                                    # 0.0.0.0:8345
```

Optionen beim Foursquare-Connector: `--data-dir`, `--db`, `--sleep`,
`--no-photos`, `--no-export`, `--verbose`. `probe` ist rein lesend und schreibt
keine Daten. Die Web-Oberfläche liest nur den Index; `index build` läuft
getrennt davor.

## Datenlayout

```
data/
├── foursquare/
│   ├── foursquare.db          Quelle (Check-ins, Venues, Fotos, Listen, Tipps, Profil, Sync-Status)
│   ├── exports/               checkins, photos, comments, likes, lists, tips, profile, venues, venue_details (CSV)
│   ├── places_api_venue_details.json  Baseline-Dump der Places API (Import-Quelle, gitignored)
│   ├── profile/               lokales Profilbild
│   ├── photos/<checkin_id>/   Bilddateien (Standalone unter photos/_standalone/)
│   └── thumbs/                320-px-Vorschaubilder für die Web-UI
├── yelp/                      yelp.db + media/ + thumbs/ (HTML-Import)
├── google/                    google.db (Quelle) + osm_cache.db (Enrichment-Cache)
├── osm/osm.db                 OSM-POI-Cache und Abfrage-Cache (Overpass/Nominatim)
├── places/place_rules.db      persistente manuelle Merge-/Unmerge-Regeln
├── places/identity.db         persistente OSM-Zuordnungen + URL-Aliase
├── index/index.db             zentraler, rebuildbarer Index
└── exports/visits.geojson     Kartenpunkte aller Quellen
```

Die Quell-DBs sind die Wahrheit. Der Index wird bei jedem `index build` neu aus
den Connectors und den Regeln in `data/places/place_rules.db` erzeugt und kann
gefahrlos gelöscht werden. Nur die Regel-DB bleibt dauerhaft.

## Web-Oberfläche

`./web.sh` startet FastAPI/Jinja auf `http://<host>:8345` (Bind `0.0.0.0`).
Die Seite zeigt standardmäßig alle Besuche in München, mit Filtern für Stadt,
Jahr, Hauptkategorie, Unterkategorie, Quelle und Freitext. Oben eine
Leaflet-Karte mit OpenStreetMap-Tiles (Leaflet liegt lokal unter
`spot_history/web/static/vendor/leaflet/`), darunter die Liste mit 60 Treffern
pro Seite. Klick auf Karte und Liste ist synchronisiert. Besuchskarten zeigen
Start- und Endzeit sowie die Aufenthaltsdauer, sofern die Quelle eine Endzeit
liefert (aktuell Google Timeline).

Die Fein-Kategorien (z. B. `German Restaurant`, `Café`, `Beer Garden`) werden
beim Index-Build in Hauptkategorien zusammengefasst (`Restaurants`, `Cafés`,
`Biergarten`, `Bars & Nightlife`, `Kultur & Sehenswürdigkeiten`, ...). Die
Feinkategorie bleibt als Unterkategorie erhalten. Foursquare liefert die
Feinkategorie; Yelp enthält im Export keine Ortskategorie. Bei Orten ohne
Kategorie wird die Hauptkategorie konservativ aus dem Ortsnamen abgeleitet und
in der UI mit `≈` markiert. Ohne Treffer bleibt `Ohne Kategorie`.

Zusätzlich gibt es eine Profilkarte mit lokal gespeichertem Avatar sowie eine
Ortsliste unter `/venues` mit Filtern und Detailseiten unter `/venues/{key}`.
Der `{key}` ist `osm-<type>-<id>` (z. B. `osm-way-247857140`) für gematchte Orte
und `local-<source>-<source_place_id>` für noch nicht gematchte Orte. Alte
Integer-URLs und frühere Schlüssel werden per 302 auf den aktuellen Key
umgeleitet.
Die Ortsliste zeigt einen Eintrag pro kanonischem Ort mit Badges für alle
Quellen. Die Detailseite zeigt Adresse, Kategorie, Koordinaten, Besuchszahl,
ersten und letzten Besuch, Fotoanzahl, Fotogalerie, die Besuchshistorie und
alle vorhandenen Quell-Links (Foursquare und/oder Yelp).

Die Navigation führt zu **Check-ins**, **Orten**, **Fotos** und **Yelp**.
Die Fotoseite `/photos` zeigt Thumbnails mit Filtern (Stadt, Jahr, Quelle,
Suche) und Pagination; Klick öffnet eine Lightbox mit dem Original.
Check-in-Karten zeigen bis zu drei Vorschaubilder, Venue-Detailseiten eine
Galerie. Yelp-Videos werden als eigener Video-Eintrag dargestellt.
Venue-Detailseiten zeigen zusätzlich Yelp-Survey-Antworten, passende
Business-Interaktionen sowie Fotos ohne Check-in, die über den Ortsnamen der
Venue zugeordnet wurden (Badge „ohne Check-in“). Kommentare ohne Check-in
werden als echte Visits behandelt: Sie erscheinen in der Check-in-Liste, der
Venue-Historie, den Zählern und – bei vorhandenen Ortskoordinaten – als
abgeleitete Kartenpunkte, jeweils mit Badge „Kommentar“. Kommentare zu einem
vorhandenen Check-in erscheinen als Kommentarzeilen am jeweiligen Visit.
Die Seite `/aktivitaet` zeigt oben die Kontodaten und das Yelp-Profilfoto als
flachen Profilblock (Profilfoto nicht mehr unter „Fotos ohne Check-in“) und
bündelt darunter Komplimente, Nachrichten, Milestones, Forumsbeiträge,
Review-/Foto-Feedback und Interaktionen.
Foto-Feedback verlinkt auf die Yelp-Fotoseite, da im Export kein lokales Bild
dazu existiert.
Sensible Felder (E-Mail, Telefon, IP-Adressen, Logins, gemeldete Inhalte)
werden importiert, aber nicht im Web angezeigt.

Der Karten-Popup zeigt Quelle, Kategorie, Adresse/Stadt und einen direkten Link
zum Ort (`/venues/{key}`), nicht mehr den Foursquare-Original-Link.

Die Ortsliste hat einen Filter **OSM-Status** (mit OSM-ID, nur lokal, ohne
OSM-ID, Konflikte). Auf der Detailseite eines nicht gematchten Orts gibt es ein
Panel **„OSM-Objekt zuordnen“** mit manueller Eingabe (`node/123`,
`way-247857140` oder OSM-URL) und – falls vorhanden – den Kandidaten aus
`osm sync` zum Übernehmen. Bei gematchten Orten lässt sich die Zuordnung dort
wieder entfernen. Jede Änderung schreibt in `data/places/identity.db` und baut
den Index sofort neu; danach leitet die Seite auf den neuen Key um. Die
POST-Endpunkte prüfen die Herkunft (Same-Origin), die Oberfläche bleibt aber
wie der Rest ohne Login und ist für LAN/interne Nutzung gedacht.

Lokale Bilder und Thumbnails werden über `/media/...` ausgeliefert. Erlaubt sind
nur Bild- und Videodateien unter `data/*/profile/`, `data/*/photos/`,
`data/*/media/` und `data/*/thumbs/`; Datenbanken, Exporte und Pfad-Traversal
werden blockiert.

Thumbnails werden mit `.venv/bin/python -m spot_history index thumbs` erzeugt
(320 px, nach neuen Fotos erneut ausführen; `--force` erzwingt Neuberechnung).
Ohne Thumbnails fällt die Web-UI auf die Originaldateien zurück.

Die Oberfläche hat **keine Authentifizierung** und ist für LAN/interne Nutzung
gedacht.

## Zentraler Index

- `sources` – welche Quellen enthalten sind
- `source_profiles` – Anzeigename, Home City, Avatar und Zähler je Quelle
- `places` – quellenspezifische Orte (`source`, `source_place_id`)
- `place_details` – Kontakt, Social-Media, Kategorien und Datenstand je Ort
- `canonical_places` – ein Ort pro realem Ort, aggregiert über Quellen, inkl.
  `canonical_key`, `category`/`category_group`/`category_group_source`,
  `osm_type`, `osm_id`, `match_status` sowie `osm_lat`/`osm_lng` aus dem
  OSM-Cache
- `canonical_aliases` – frühere Keys für Redirects auf den aktuellen Key
- `place_links` – Quell-Identitäten und Links je kanonischem Ort
- `merge_candidates` – nicht automatisch angewandte Merge-Vorschläge
- `source_records` – Detail- und Aktivitätsdaten je Quelle (Yelp-Surveys,
  Interaktionen, Komplimente, Nachrichten, Feedback u. a.)
- `visits` – Besuche/Check-ins mit Zeit, Koordinaten, Notiz, Quell-URL
  (`place_id` = Quell-Ort, `canonical_place_id` = kanonischer Ort)
- `photos` – Fotometadaten + lokaler Pfad
- `index_meta` – Build-Zeitpunkt

`visits.geojson` ist eine `FeatureCollection` mit einem Punkt pro Visit
(Koordinaten als `[lng, lat]`), inklusive Quelle, Ort, Kategorie,
Hauptkategorie, Zeit, kanonischem Ort, Quellenliste und lokalen Fotopfaden. Das
reicht für Leaflet/MapLibre mit OSM-Tiles.

## Zusammengeführte Orte

Der Index bildet pro realem Ort einen kanonischen Ort. Jede Quelle bleibt als
eigener Eintrag in `place_links` erhalten, inklusive `source`, stabiler
`source_place_id` und – falls vorhanden – Original-URL. Damit zeigt ein
gemergter Ort z. B. den Foursquare-Venue-Link und den Yelp-Business-Link
gleichzeitig.

Automatisch gemergt wird nur konservativ:

1. normalisierter Name gleich und Koordinaten höchstens 50 m auseinander
   (genau ein Kandidat), oder
2. normalisierter Name gleich, keine Yelp-Koordinaten und genau ein
   Foursquare-Ort mit derselben Stadt.

Alles andere landet in `merge_candidates` und wird nicht automatisch
zusammengeführt, z. B. gleicher Name mit mehreren Kandidaten oder gleicher Name
in derselben Stadt, aber mit weit auseinanderliegenden Koordinaten.

Sobald eine OSM-ID zugeordnet ist, wird sie zum harten Identitätsmerkmal:
Quell-Orte mit derselben OSM-ID werden immer zusammengeführt, Quell-Orte mit
unterschiedlichen OSM-IDs nie. Die Zuordnungen liegen persistent in
`data/places/identity.db` und überleben jeden Index-Rebuild; dort werden auch
Aliase für Redirects gespeichert. Manuelle
Entscheidungen liegen dauerhaft in
`data/places/place_rules.db` und überleben jeden Index-Rebuild:

```bash
# Zahlen und offene Vorschläge
python3 -m spot_history places stats
python3 -m spot_history places candidates [--source yelp] [--json]

# dauerhaft manuell zusammenführen / Auto-Merge abschalten / Regel löschen
python3 -m spot_history places merge yelp:<place_key> foursquare:<venue_id>
python3 -m spot_history places unmerge yelp:<place_key>
python3 -m spot_history places reset yelp:<place_key>
```

`merge`, `unmerge` und `reset` bauen den Index standardmäßig sofort neu
(`--no-build` überspringt das). Aktueller Datenstand: 1320 Quell-Orte, 1194
kanonische Orte, 126 automatische Merges, 14 offene Vorschläge.

Bekannte Grenzen:

- Yelp-Orte werden intern über den normalisierten Namen gruppiert
  (`place_key`). Gleichnamige Orte in verschiedenen Städten sind damit schon in
  der Yelp-Quelle ein Ort.
- Auto-Merges über Name + Stadt sind Heuristiken; `places unmerge` ist der
  Korrekturweg.
- Nur 16 der 284 Yelp-Orte haben im Export eine echte Business-URL (aus
  Lesezeichen/Sammlungen, die auch einen Check-in haben). Bei den übrigen
  bleibt der Yelp-Link leer.
- Öffentliche Orts-URLs verwenden `canonical_key` (`osm-...` oder `local-...`),
  nicht den internen Integer-`id`. Frühere Keys bleiben über
  `canonical_aliases` (und `data/places/identity.db`) als Redirect erhalten.

## OSM-Zuordnung

```bash
# Voraussetzung: aktueller Index (liefert Namen, Koordinaten und Quell-Links)
python3 -m spot_history index build

# Offene Orte über Overpass matchen (Fallback auf Nominatim je Batch)
python3 -m spot_history osm sync

# Alternativen für Tests oder wenn Overpass nicht erreichbar ist
python3 -m spot_history osm sync --limit 25 --dry-run --verbose
python3 -m spot_history osm sync --provider nominatim

# Danach OSM-Keys in den Index übernehmen
python3 -m spot_history index build

# Status, offene Kandidaten und nicht gematchte Orte
python3 -m spot_history osm stats
python3 -m spot_history places osm-candidates [--json]
python3 -m spot_history places unmatched [--city München] [--json]

# Kandidat oder manuelle OSM-ID übernehmen / Zuordnung entfernen
python3 -m spot_history places osm-accept foursquare:<venue_id>
python3 -m spot_history places assign-osm foursquare:<venue_id> way/247857140
python3 -m spot_history places unassign-osm foursquare:<venue_id>
```

- `osm sync` liest den Index nur und schreibt ausschließlich in
  `data/osm/osm.db` (POI-/Abfrage-Cache) und `data/places/identity.db`
  (Zuordnungen/Kandidaten). Es werden keine Quell-DBs verändert.
- Overpass wird gebündelt pro `--chunk`-Punkte abgefragt (Default 40, Radius
  120 m). Schlägt ein Batch fehl, werden die betroffenen Orte über Nominatim
  nachgeladen; Nominatim wird mit maximal ca. 1 Request/s und Cache genutzt.
- Automatische Zuordnung nur bei eindeutigem, hohem Score
  (Name + Distanz/Stadt). Alles andere landet als Kandidat in der
  Identitäts-DB und muss über `osm-accept`, `assign-osm` oder die Web-UI
  bestätigt werden.
- `--force` ignoriert den Abfrage-Cache; `--dry-run` matcht nur und schreibt
  keine Zuordnungen.
- Gematchte Orte übernehmen die gecachten OSM-Koordinaten in den Index
  (`osm_lat`/`osm_lng`). Hauptkarte, Venue-Detailseite und `visits.geojson`
  bevorzugen diese Position; fehlt sie, gelten weiter die Quellkoordinaten.
  Die Quellkoordinaten bleiben in `lat`/`lng` erhalten.
- OSM-Daten stehen unter ODbL; bei öffentlicher Darstellung ist
  „© OpenStreetMap contributors“ erforderlich. Nominatim ist kein
  Massen-Scraper – Cache und Rate-Limit beibehalten.

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
  (5 Listen, 150 Listeneinträge; bei „My Liked Places“ 53 von 54), `tips`
  (11, inkl. Details wie Sprache, Saves, Titel) und alle Nutzerfotos
  (577; 562 mit Check-in, 15 standalone)
- Tastes (`/v2/users/self/tastes`, 17 Einträge) und die Tip-Details
  (`/v2/tips/<id>`) werden vollständig inklusive Roh-JSON gespeichert
- Nutzerprofil inklusive Zählern und lokal gespeichertem Avatar
  (`data/foursquare/profile/`)
- Bekannte API-Lücken werden in `sync_diagnostics` dokumentiert und in `stats`
  angezeigt
- Venue-Details (Öffnungszeiten, Preis, Rating, Venue-Fotos/-Tipps) sind aktuell
  nicht abrufbar: `/v2/venues/{id}` liefert 402, die Places-API mit
  Premium-Feldern, `/photos`, `/tips` und `/places/ask` 429. Die 1036
  Venue-Snapshots aus den Check-ins reichen für Liste und Detailseite.
- Die Places-API (`https://places-api.foursquare.com`, `FOURSQUARE_SERVICE_API_KEY`)
  liefert dagegen für **alle 1036 Venues** Baseline-Details (200): aktueller
  Name, Adresse, Stadt/Region/PLZ/Land, Koordinaten, Kategorien, Telefon (747),
  Website (748), E-Mail (395) und Social-Media-IDs. Der Client liegt in
  `places_api.py`.
- Die Baseline wurde einmalig in `data/foursquare/places_api_venue_details.json`
  gesichert und mit `foursquare venue-import` in die Tabelle `venue_details`
  importiert (1036 Einträge, 811 mit Kontakt). Der Import ist idempotent, macht
  keine API-Calls und überschreibt keine bestehenden Venue-Snapshots.
- Die Detailseite `/venues/{id}` zeigt daraus Telefon, Website, E-Mail,
  Social-Links, alle Kategorien und den Datenstand; leere Felder werden
  ausgeblendet.
- Hinweis zum Kontingent: Der einmalige Abruf aller 1036 Venues hat das
  freePro-Kontingent aufgebraucht; weitere Detail-Calls liefern bis zum
  Reset/Credits 429. Der Import aus JSON verbraucht kein Kontingent.
- `places/search`, `autocomplete`, `places/match` und
  `geotagging/candidates` antworten mit 200, `places/resolve` mit 401
  (fehlende Berechtigung).
- `foursquare probe` prüft die Konto-Endpunkte rein lesend; `friends`/`badges`
  liefern 404, `events` 403, `likes`/`herenow` 402. Zusätzlich werden
  Places-API-Baseline, Premium-Felder, Fotos und Tipps geprüft.

## Yelp-Import

```bash
python3 -m spot_history yelp import
python3 -m spot_history index build       # Foursquare-Index für Namensmatches
python3 -m spot_history yelp geocode      # Nominatim, max. 1 Request/s
python3 -m spot_history index build       # Städte in den Index übernehmen
python3 -m spot_history yelp stats
```

- 462 Check-ins, 284 Orte, 92 mit Quellkoordinaten, 270 Medien
- 20 Kommentare ohne Check-in werden als zusätzliche Visits importiert
  (Badge „Kommentar“); 8 bereits verknüpfte Kommentare erscheinen am
  jeweiligen Check-in-Visit
- Zusätzlich importiert: 39 Survey-Antworten, 45 Business-Interaktionen,
  36 Review-Feedback, 8 Foto-Feedback, 143 Komplimente, 25 Nachrichten,
  3 Milestones, 2 Forumsbeiträge, 1 Profilfrage und die Kontodaten
  (10 Felder; sensible Felder werden nicht im Web angezeigt)
- 17 Quelltabellen ohne Daten (u. a. Reservierungen, Wartelisten, Tips) werden
  mit Headern und Zeilenzahl in `source_tables` dokumentiert
- 16 Yelp-Orte haben aus Lesezeichen/Sammlungen eine Business-URL; sie wird
  beim Import in `places.place_url` gespeichert und als Quell-Link in den Index
  übernommen
- Das Yelp-`Datum` ist UTC; der Export enthält keinen Orts-Offset. In
  `yelp.db` bleibt `tz_offset` deshalb `NULL`. Der Index löst die Lokalzeit auf
  (gleicher Ort, Orts-Zeitzone, Stadt/Land, Umfeld-Mehrheit oder naher
  Foursquare-Check-in); aktuell sind alle 462 Yelp-Visits aufgelöst.
- 135 Yelp-Kartenpunkte: 92 mit Quellkoordinaten, 43 über den Ort abgeleitet
  (Badge „Ort abgeleitet“ in Liste und Karte)
- 316 von 462 Visits mit Stadt: 135 über Nominatim, 181 über exakte
  Namensmatches gegen Foursquare-Index und Export-URLs
- 146 Visits bleiben ohne Stadt, 327 ohne Kartenpunkt
- Der Geocoding-Cache liegt unter `data/yelp/` und ist gitignored;
  `--force` erzwingt neue Nominatim-Requests

## Google-Timeline-Import

Google Timeline liegt seit der Migration 2024/25 nur noch auf dem Gerät. Es gibt
keine API und keinen Web-Export; Google Takeout enthält nur den Altbestand vor
der Migration. Der Import ist deshalb zweistufig: manueller Export aus der
Google-Maps-App, danach automatischer Import und OSM-Anreicherung.

### Export vom iPhone

1. Google Maps → Profil → Einstellungen → Standort & Datenschutz →
   Timeline-Daten exportieren.
2. Im iOS-Share-Sheet „In Dateien sichern“; die Datei heißt
   `location-history.json` (oder `Timeline.json`).
3. In einen Ordner speichern, der zu Linux synchronisiert (Dropbox, Nextcloud,
   Google Drive + `rclone`; iCloud Drive ist nicht direkt mountbar).
4. Auto-Delete in Timeline aus oder mindestens 36 Monate, sonst verschwinden
   Daten vor dem nächsten Export.

Den Pfad setzt man in `.env`:

```env
GOOGLE_TIMELINE_INBOX=/path/to/google-timeline
```

Der Importer erkennt Dateien am Inhalt, nicht am Namen. Unterstützt werden der
iPhone-Array-Export, `semanticSegments` (Android/Phone), Legacy-Takeout
(`timelineObjects`/`placeVisit`) und GeoJSON aus Bewertungen/Gespeicherten
Orten. `Records.json` (rohe GPS-Pings) und `@eaDir` werden übersprungen.

### Import und OSM-Anreicherung

```bash
python3 -m spot_history google scan
python3 -m spot_history google import
python3 -m spot_history google osm
python3 -m spot_history google stats
```

- `google import` baut `data/google/google.db` bei jedem Lauf neu aus allen
  Dateien im Inbox-Ordner auf. Doppelte Visits werden über stabile IDs
  zusammengefasst; Google-Place-IDs mit identischen Koordinaten (≤15 m) werden
  zu einem Ort konsolidiert.
- `Home` und `Work` werden als „Zuhause“/„Arbeit“ geführt; Adressen dieser
  Orte werden nicht angezeigt.
- `google osm` nutzt zuerst den vorhandenen Cache in `data/osm/osm.db`
  (nur lesend), danach Overpass (`nwr(around:55)[name]`) und als Fallback
  Nominatim-Reverse. Ergebnisse landen in `data/google/osm_cache.db`.
- Automatisch übernommen werden nur eindeutige Treffer ≤25 m. Treffer zwischen
  25 und 50 m sowie Mehrdeutiges landen als Kandidaten in
  `google osm-candidates`; dort lassen sie sich mit `google osm-accept`
  übernehmen.
- Bewertungen und gespeicherte Orte werden als `source_records` importiert,
  auf Venue-Detailseiten angezeigt und innerhalb von 15 m als Namenshilfe
  verwendet (eine Google-Bewertung kann einen ungenauen OSM-Namen ersetzen).
- Manuelle Namen: `google name <cluster_key> "Name"` (optional mit
  `--address`, `--city`, `--category`). Überschreibungen überleben jeden
  Re-Import.
- Es werden keine kostenpflichtigen APIs verwendet. Overpass/Nominatim werden
  mit Cache, Batch-Größe und Pausen im Fair-Use-Rahmen genutzt; OSM-Daten
  stehen unter ODbL („© OpenStreetMap contributors“).
- Der Google-Maps-Link bleibt als `place_url` erhalten
  (`...?api=1&query_place_id=...`), auch wenn kein Name gefunden wurde.

Bekannte Grenzen: Google liefert nur Place-ID + Koordinaten, keine Namen. Es
gibt keine Google-ID→OSM-ID-Abbildung; die Zuordnung läuft über Koordinaten.
In dichten Lagen kann der nächste OSM-POI falsch sein, deshalb werden
25–50-m-Treffer nur als Kandidaten gespeichert. Viele Orte erhalten statt eines
Venue-Namens eine Adresse (Straße + Hausnummer). Der iPhone-Export selbst
bleibt manuell; `google.sh` übernimmt Import, OSM-Anreicherung und Index-Rebuild.

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
- Der Google-Timeline-Export bleibt manuell (iOS-Share-Sheet); `google.sh`
  übernimmt danach Import, OSM-Anreicherung und Index-Rebuild.
- Kein Cron/Daemon: Sync und Index werden manuell aufgerufen.
