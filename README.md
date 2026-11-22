# 📍 spot-history-manager

Collects check-in and visit histories from multiple services, stores each source
in its own SQLite database, and builds a central, rebuildable index plus GeoJSON
for an OpenStreetMap map.

Currently implemented:

- 🟦 **Foursquare/Swarm** (API sync)
- 🟥 **Yelp** (one-time HTML import)
- 🟩 **Google Timeline** (iPhone/Takeout import with free OSM enrichment)

## ✅ Requirements

- Python 3.10+; sync, index builds, and imports use only the standard library
- For the web interface: FastAPI, uvicorn, Jinja2, and Pillow from `requirements.txt`
- `.env` with `FOURSQUARE_OAUTH_TOKEN`

```bash
cp .env.example .env
chmod 600 .env
```

```env
FOURSQUARE_OAUTH_TOKEN=...
```

## 🚀 Usage

```bash
# Foursquare: first run (full history)
python3 -m spot_history foursquare backfill

# Later: only new items since the last sync
./sync.sh
# or:
python3 -m spot_history foursquare sync

# Summary
python3 -m spot_history foursquare stats

# Check API endpoints read-only (v2 + Places API)
python3 -m spot_history foursquare probe

# Import the Places API baseline once from JSON (no API calls)
python3 -m spot_history foursquare venue-import

# Export, inspect, import, and enrich Google Timeline via OSM
python3 -m spot_history google scan     --input "$GOOGLE_TIMELINE_INBOX"
python3 -m spot_history google import   --input "$GOOGLE_TIMELINE_INBOX"
python3 -m spot_history google osm
python3 -m spot_history google stats

# Build the central index + GeoJSON for all sources
python3 -m spot_history index build

# Inspect and manually correct canonical places
python3 -m spot_history places stats
python3 -m spot_history places candidates
python3 -m spot_history places audit
python3 -m spot_history places merge yelp:<place_key> foursquare:<venue_id>

# Thumbnails for the web UI (Pillow, once or after new photos)
.venv/bin/python -m spot_history index thumbs

# Web interface (list + OpenStreetMap map + photos)
.venv/bin/pip install -r requirements.txt   # once
./web.sh                                    # 0.0.0.0:8345
```

Foursquare connector options: `--data-dir`, `--db`, `--sleep`, `--no-photos`,
`--no-export`, `--verbose`. `probe` is read-only and writes no data. The web
interface only reads the index; run `index build` separately beforehand.

## 🐳 Docker

GitHub Actions publishes the image to the GitHub Container Registry:

```bash
docker pull ghcr.io/skoelle/spot-history-manager:latest
docker run --rm \
  -p 8345:8345 \
  -v "$PWD/db:/app/db" \
  -v "$PWD/data:/app/data" \
  -v "$PWD/backup:/app/backup" \
  -e SPOT_HISTORY_BACKUP_ROOT=/app/backup \
  ghcr.io/skoelle/spot-history-manager:latest
```

Three directories are mounted; the image contains no data:

- `db/` → `/app/db`: only the SQLite databases, keep this on the Docker host's
  local disk (default: `./db`).
- `data/` → `/app/data`: photos, thumbnails, media, exports, the Google inbox,
  and sync logs. This can be a Synology/NAS bind mount.
- `backup/` → `/app/backup`: daily SQLite backups, keep this on the Docker
  host's local disk (default: `./backup`).

The container runs as UID/GID 1000, so all mounted directories must be
writable by that user. The web UI has no authentication; put it behind a reverse
proxy if you expose it.

For a persistent deployment, copy `docker-compose.example.yml` to
`docker-compose.yml` and adjust the volume paths (`./db` and `./backup` local,
`./data` on the NAS). Create `./backup` before the first start; Docker creates a
missing bind-mount directory as root, which UID 1000 cannot write to:

```bash
mkdir -p backup
sudo chown 1000:1000 backup
```

The image does not contain a `.env`; Compose interpolates the tokens from
the `.env` file next to `docker-compose.yml` and passes them into the container.
`SPOT_HISTORY_DATA_ROOT`, `SPOT_HISTORY_DB_ROOT`, and
`SPOT_HISTORY_BACKUP_ROOT` point the app at the mounts.

### 🔐 Permissions on NAS mounts

Synology and other NAS systems map their own UIDs/ACLs onto bind mounts. Even if
the web UI can read the data, a sync can fail with:

```
PermissionError: [Errno 13] Permission denied: '/app/data/sync'
```

The container runs as UID/GID 1000, so the host directories must be writable by
that user. Check the effective ownership inside the container:

```bash
docker exec spot-history-manager id
docker exec spot-history-manager ls -ldn /app/data /app/db /app/backup
```

Fix it on the host once (`<data>` and `<db>` are the host paths from the Compose
file):

```bash
sudo chown -R 1000:1000 <data> <db> backup
sudo chmod -R u+rwX,g+rwX <data> <db> backup
```

If DSM's ACLs override POSIX ownership, either grant write access to UID 1000
via File Station/`synoacltool`, or run the container with the NAS UID/GID
(`user: "<uid>:<gid>"` in Compose) and make `db/` and `backup/` match. The
Python code itself does not depend on a specific UID.

Verify the fix without waiting for a sync:

```bash
docker exec --user app spot-history-manager sh -c \
  'mkdir -p /app/data/sync/logs && touch /app/data/.write-test && rm /app/data/.write-test'
```

### 🔄 Sync buttons in the web UI

The **Sync** page (`/sync`) starts jobs directly inside the container:

- Foursquare: incremental sync, full backfill, pending photo downloads
- Yelp: HTML export import, geocoding
- Google: Timeline import from `data/google/inbox` (or `GOOGLE_TIMELINE_INBOX`),
  OSM enrichment
- OpenStreetMap: `osm sync`
- Index: rebuild, thumbnails

Each sync job automatically runs `index build` afterwards, so the web UI shows
new data immediately. Only one job runs at a time; the page shows status,
duration, a log excerpt, and a cancel button. Logs are also written to
`data/sync/logs/`.

The page also shows the time of the last successful import/sync for each
source, read directly from the source databases.

There is no automatic schedule. The web UI has no authentication, so protect it
with a reverse proxy if it is exposed. For Google imports, place the export in
`data/google/inbox` or mount it separately and point `GOOGLE_TIMELINE_INBOX` to
the path inside the container. The Yelp import expects the export in
`data/yelp/export` or at `YELP_EXPORT_DIR`.

## 🗂️ Data layout

```
db/                            local SQLite databases only
├── foursquare/foursquare.db   source (check-ins, venues, photos, lists, tips, profile, sync state)
├── yelp/yelp.db               source (HTML import)
├── google/google.db           source (Timeline import)
├── google/osm_cache.db        OSM enrichment cache
├── osm/osm.db                 OSM POI cache and query cache (Overpass/Nominatim)
├── places/place_rules.db      persistent manual merge/unmerge rules
├── places/identity.db         persistent OSM assignments + URL aliases
└── index/index.db             central, rebuildable index

data/                          can live on a NAS
├── foursquare/
│   ├── exports/               checkins, photos, comments, likes, lists, tips, profile, venues, venue_details (CSV)
│   ├── places_api_venue_details.json  baseline dump from the Places API (import source)
│   ├── profile/               local profile picture
│   ├── photos/<checkin_id>/   image files (standalone under photos/_standalone/)
│   └── thumbs/                320 px thumbnails for the web UI
├── yelp/                      media/ + thumbs/ (HTML import)
├── google/inbox/              Google Timeline export input
├── exports/visits.geojson     map points from all sources
└── sync/logs/                 logs of the web UI sync jobs

backup/                        local SQLite backups (default mount: /app/backup)
└── YYYY-MM-DD/                one folder per day (see below)
```

When `SPOT_HISTORY_BACKUP_ROOT` is not set, backups fall back to
`data/backup/`, which also works but puts them on the NAS mount.

The source databases are the source of truth. The index is rebuilt from the
connectors and the rules in `db/places/place_rules.db` on every `index build`
and can be safely deleted. Only the rules database is persistent.

### 💾 Backups

`python -m spot_history backup` copies every database under `db/` with the
SQLite online backup API (WAL-safe, also while the app is running), verifies it
with `PRAGMA quick_check`, and writes it to `YYYY-MM-DD/` below
`SPOT_HISTORY_BACKUP_ROOT` (default: `<data-root>/backup`, in Docker
`/app/backup`). The `--keep-days` option (default `3`) keeps the three most
recent daily backups.

In Docker, run it from the host via systemd or cron, for example:

```bash
docker exec spot-history-manager python -m spot_history backup \
  --db-root /app/db --keep-days 3
```

The destination comes from the container's `SPOT_HISTORY_BACKUP_ROOT`
environment variable, so no `--dest` is needed. Use `--dest` only to override
it for a one-off run.

Example units are in `deploy/spot-history-backup.service` and
`deploy/spot-history-backup.timer` (daily at 03:30, `Persistent=true`):

```bash
sudo cp deploy/spot-history-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now spot-history-backup.timer
```

Cron alternative on the host:

```cron
30 3 * * * /usr/bin/docker exec spot-history-manager python -m spot_history backup --db-root /app/db --keep-days 3
```

With the default `./backup` mount, backups stay on the Docker host while
`data/` lives on the NAS. Retention runs after every backup; folders older than
the configured number of days are removed.

### 🔁 Migration from the old layout

Older versions stored the databases inside `data/`. Stop the container and move
them once; the non-database files stay where they are:

```bash
mkdir -p db/{foursquare,yelp,google,osm,places,index}
mv data/foursquare/foursquare.db* db/foursquare/
mv data/yelp/yelp.db* db/yelp/
mv data/google/google.db* data/google/osm_cache.db db/google/
mv data/osm/osm.db db/osm/
mv data/places/*.db db/places/
mv data/index/index.db db/index/
rmdir data/osm data/places data/index 2>/dev/null || true
chown -R 1000:1000 db data
mkdir -p backup
chown -R 1000:1000 backup
```

Then set `SPOT_HISTORY_DB_ROOT` (in Compose: `/app/db`) and
`SPOT_HISTORY_BACKUP_ROOT` (in Compose: `/app/backup`) and start the container
again. A `backup` run right before moving is recommended.

## 🖥️ Web interface

`./web.sh` starts FastAPI/Jinja on `http://<host>:8345` (bind `0.0.0.0`).
By default, the page shows all visits in Munich, with filters for city, year,
main category, subcategory, source, and free text. At the top is a Leaflet map
with OpenStreetMap tiles (Leaflet is vendored locally under
`spot_history/web/static/vendor/leaflet/`); below it is the list with 60 results
per page. Map and list selection are synchronized. Visit cards show start and
end time as well as duration when the source provides an end time (currently
Google Timeline).

Fine-grained categories (e.g. `German Restaurant`, `Café`, `Beer Garden`) are
grouped into main categories during the index build (`Restaurants`, `Cafés`,
`Biergarten`, `Bars & Nightlife`, `Kultur & Sehenswürdigkeiten`, ...). The fine
category is kept as a subcategory. Foursquare provides the fine category;
Yelp's export contains no place category. For places without a category, the
main category is conservatively derived from the place name and marked with `≈`
in the UI. If there is no match, the UI shows `Ohne Kategorie`.

There is also a profile card with a locally stored avatar, plus a place list at
`/venues` with filters and detail pages at `/venues/{key}`. The `{key}` is
`osm-<type>-<id>` (e.g. `osm-way-247857140`) for matched places and
`local-<source>-<source_place_id>` for places that have not been matched yet.
Legacy integer URLs and previous keys redirect with 302 to the current key.
The place list shows one entry per canonical place with badges for all sources.
The detail page shows address, category, coordinates, visit count, first and
last visit, photo count, photo gallery, visit history, and all available source
links (Foursquare and/or Yelp).

The navigation leads to **Check-ins**, **Orte**, **Fotos**, **Foursquare**,
**Yelp**, and **Google**.
The photo page `/photos` shows thumbnails with filters (city, year, source,
search) and pagination; clicking opens a lightbox with the original image.
Check-in cards show up to three preview images; venue detail pages show a
gallery. Yelp videos are shown as a separate video entry.
Venue detail pages also show Yelp survey answers, matching business
interactions, and photos without a check-in that were assigned to the venue via
the place name (badge `ohne Check-in`). Comments without a check-in are treated
as real visits: they appear in the check-in list, the venue history, the
counters, and — when place coordinates exist — as derived map points, each with
the `Kommentar` badge. Comments on an existing check-in appear as comment lines
on the respective visit.
The `/foursquare` page shows the Foursquare profile and bundles tips, lists
(including list entries), tastes, and check-in comments and likes. Tips and list
memberships (including `Geliked`/`Gespeichert`) also appear at the top of the
respective venue detail page; check-in comments and likes are shown on the
visit.
The `/yelp` page (formerly `/aktivitaet`; old links redirect) shows account data
and the Yelp profile photo as a flat profile block at the top (the profile photo
is no longer under `Fotos ohne Check-in`) and bundles compliments, messages,
milestones, forum posts, review/photo feedback, and interactions below it.
Photo feedback links to the Yelp photo page because the export contains no
local image for it.
Sensitive fields (email, phone, IP addresses, logins, reported content) are
imported but not shown in the web UI.

The map popup shows source, category, address/city, and a direct link to the
place (`/venues/{key}`), no longer the original Foursquare link.

The place list has an **OSM-Status** filter (with OSM ID, local only, without
OSM ID, conflicts). On the detail page of an unmatched place there is an
**`OSM-Objekt zuordnen`** panel with manual input (`node/123`,
`way-247857140`, or an OSM URL) and — if available — candidates from `osm sync`
that can be accepted. For matched places, the assignment can be removed there.
Every change writes to `db/places/identity.db` and immediately rebuilds the
index; afterwards the page redirects to the new key. The POST endpoints check
the origin (same-origin), but the UI, like the rest, has no login and is
intended for LAN/internal use.

Local images and thumbnails are served via `/media/...`. Only image and video
files under `data/*/profile/`, `data/*/photos/`, `data/*/media/`, and
`data/*/thumbs/` are allowed; databases, exports, and path traversal are
blocked.

Thumbnails are generated with `.venv/bin/python -m spot_history index thumbs`
(320 px; rerun after new photos; `--force` forces recalculation). Without
thumbnails, the web UI falls back to the original files.

The interface has **no authentication** and is intended for LAN/internal use.

## 🧭 Central index

- `sources` – which sources are included
- `source_profiles` – display name, home city, avatar, and counters per source
- `places` – source-specific places (`source`, `source_place_id`)
- `place_details` – contact, social media, categories, and data timestamp per place
- `canonical_places` – one place per real-world place, aggregated across sources,
  including `canonical_key`, `category`/`category_group`/`category_group_source`,
  `osm_type`, `osm_id`, `match_status`, and `osm_lat`/`osm_lng` from the OSM cache
- `canonical_aliases` – previous keys for redirects to the current key
- `place_links` – source identities and links per canonical place
- `merge_candidates` – merge suggestions that were not applied automatically
- `source_records` – detail and activity data per source (Yelp surveys,
  interactions, compliments, messages, feedback, etc.)
- `visits` – visits/check-ins with time, coordinates, note, source URL
  (`place_id` = source place, `canonical_place_id` = canonical place)
- `photos` – photo metadata + local path
- `index_meta` – build timestamp

`visits.geojson` is a `FeatureCollection` with one point per visit (coordinates
as `[lng, lat]`), including source, place, category, main category, time,
canonical place, source list, and local photo paths. This is enough for
Leaflet/MapLibre with OSM tiles.

## 🔗 Merged places

The index creates one canonical place per real-world place. Each source remains
as its own entry in `place_links`, including `source`, stable `source_place_id`,
and — if available — the original URL. This means a merged place shows, for
example, the Foursquare venue link and the Yelp business link at the same time.

Automatic merging is conservative:

1. normalized name equal and coordinates at most 50 m apart (exactly one
   candidate), or
2. normalized name equal, no Yelp coordinates, and exactly one Foursquare place
   in the same city, or
3. name variant with similarity ≥ 0.9 and coordinates at most 25 m apart
   (exactly one unambiguous match, compatible category).

For variant 3, typographic apostrophes, umlaut spellings
(`Femörefortet`/`Femorefortet`), number words (`Sechzehn`/`16`), bracket
aliases, and generic prefixes (`Wirtshaus`, `Restaurant`, `Café`,
`Gartencenter`, ...) are normalized. Name components that are fully contained in
a longer name count as a strong match.

Everything else ends up in `merge_candidates` and is not merged automatically,
e.g. the same name with multiple candidates, the same name in the same city with
far-apart coordinates, or different OSM IDs.

Once an OSM ID is assigned, it becomes a hard identity: source places with the
same OSM ID are always merged, and source places with different OSM IDs never
are. Assignments are stored persistently in `db/places/identity.db` and
survive every index rebuild; aliases for redirects are stored there as well.
Manual decisions are stored persistently in `db/places/place_rules.db` and
survive every index rebuild:

```bash
# Counts, open suggestions, and suspect cases
python3 -m spot_history places stats
python3 -m spot_history places candidates [--source yelp] [--json]
python3 -m spot_history places audit [--source google] [--radius 50] [--json]

# Permanently merge manually / disable auto-merge / delete a rule
python3 -m spot_history places merge yelp:<place_key> foursquare:<venue_id>
python3 -m spot_history places unmerge yelp:<place_key>
python3 -m spot_history places reset yelp:<place_key>
```

`places audit` is read-only: it looks for places that come from only one source
but still have a nearby place from another source with a similar name. Its
output and `--json` are suitable for before/after comparisons after matching
changes.

The venue detail page also offers an **`Ort zusammenführen`** panel: nearby
places with distance and name similarity can be merged directly; already merged
source places can be split there again. Both write rules to
`db/places/place_rules.db` and rebuild the index.

`merge`, `unmerge`, and `reset` rebuild the index immediately by default
(`--no-build` skips this). Current data state: 2462 source places, 2190
canonical places, 254 merged places (2 of them manual), and 38 open merge
suggestions.

Known limits:

- Yelp places are grouped internally by normalized name (`place_key`). Places
  with the same name in different cities are therefore already one place within
  the Yelp source.
- Auto-merges via name + city are heuristics; `places unmerge` is the correction
  path.
- Different OSM IDs remain a hard conflict and are never merged automatically.
  Such cases appear in the audit and can be merged manually via the UI panel.
- Address-named Google clusters (street + house number) are hard to match
  automatically by name; they remain candidates for manual merging.
- Only 16 of the 284 Yelp places have a real business URL in the export (from
  bookmarks/collections that also have a check-in). For the rest, the Yelp link
  remains empty.
- Public place URLs use `canonical_key` (`osm-...` or `local-...`), not the
  internal integer `id`. Previous keys remain available as redirects via
  `canonical_aliases` (and `db/places/identity.db`).

## 🌍 OSM matching

```bash
# Prerequisite: current index (provides names, coordinates, and source links)
python3 -m spot_history index build

# Match open places via Overpass (fallback to Nominatim per batch)
python3 -m spot_history osm sync

# Alternatives for tests or when Overpass is unreachable
python3 -m spot_history osm sync --limit 25 --dry-run --verbose
python3 -m spot_history osm sync --provider nominatim

# Offline rematch against the existing POI cache (no network)
python3 -m spot_history osm rematch --dry-run --verbose
python3 -m spot_history osm rematch

# Network fallback: search for venues without OSM ID and without a candidate via Nominatim
python3 -m spot_history osm net-scan --dry-run --verbose
python3 -m spot_history osm net-scan --limit 50

# Address successors and contact matches (candidates only)
python3 -m spot_history osm address-scan --dry-run --verbose
python3 -m spot_history osm contact-scan --dry-run --verbose

# Run all stages, then mark venues without coordinates as no_coords
python3 -m spot_history osm deep-scan --dry-run --verbose
python3 -m spot_history osm deep-scan

# Then copy OSM keys into the index
python3 -m spot_history index build

# Status, open candidates, and unmatched places
python3 -m spot_history osm stats
python3 -m spot_history places osm-candidates [--json]
python3 -m spot_history places unmatched [--city München] [--json]
python3 -m spot_history places unmatched --kind venue --without-candidate

# Accept a candidate or manual OSM ID / remove an assignment
python3 -m spot_history places osm-accept foursquare:<venue_id>
python3 -m spot_history places assign-osm foursquare:<venue_id> way/247857140
python3 -m spot_history places unassign-osm foursquare:<venue_id>

# Persisted OSM states for confirmed cases
python3 -m spot_history places osm-state foursquare:<venue_id> no_osm --note "..."
python3 -m spot_history places osm-state-clear foursquare:<venue_id>
python3 -m spot_history places osm-states
python3 -m spot_history places mark-unresolved --state no_osm --dry-run
```

- `osm sync` only reads the index and writes exclusively to `db/osm/osm.db`
  (POI/query cache) and `db/places/identity.db` (assignments/candidates).
  Source databases are not modified.
- Overpass is queried in batches per `--chunk` points (default 40, radius 120 m).
  If a batch fails, the affected places are fetched via Nominatim; Nominatim is
  used with at most about 1 request/s and a cache.
- Automatic assignment only for an unambiguous, high score against the current
  OSM name (name + distance/city). Matches via old/alternative names
  (`old_name`, `alt_name`, `brand`, `operator`, `name:*`) or fuzzy name
  comparison always end up as candidates in the identity DB and must be
  confirmed via `osm-accept`, `assign-osm`, or the web UI.
- `osm rematch` evaluates open places exclusively against `db/osm/osm.db` (no
  Overpass/Nominatim) and can therefore add candidates from the cache later.
  Address/street names are tracked as `place_kind = address` and are not matched
  as venues.
- `osm net-scan` searches only for venues without an OSM ID and without an
  existing candidate via Nominatim (name + city + address). Even distant matches
  (up to `--max-distance`, default 2000 m) are only stored as candidates, never
  assigned automatically. The cache and rate limit are respected; `--force`
  ignores the Nominatim cache.
- `osm address-scan` looks up the street address of unresolved venues and stores
  valid OSM venues within 150 m as `address_match` candidates. Only matching
  category families (e.g. restaurants/cafés/bars) are accepted, so a successor
  at the same address can be suggested without matching a bakery to a doctor's
  office. Candidates are never assigned automatically.
- `osm contact-scan` matches website domains, normalized phone numbers and
  e-mail addresses from `place_details` against OSM contact tags within 2 km.
  Social and directory domains are ignored; matches are candidates only.
- `osm deep-scan` runs rematch, name, address and contact stages in order and
  marks open venues without coordinates as `no_coords` unless
  `--no-mark-no-coords` is given.
- `places osm-state` persists `open`, `no_osm`, `closed`, `no_coords` or
  `ignored` per source place; the index exposes this as `place_state` and the UI
  has status filters. `places mark-unresolved` can bulk-mark the remaining open
  venues without OSM ID and candidate (default `no_osm`).
- `--force` ignores the query cache; `--dry-run` only matches and writes no
  assignments. The place list has filters for place kind (venue/address) and
  "without OSM suggestion".
- Matched places take the cached OSM coordinates into the index
  (`osm_lat`/`osm_lng`). The main map, venue detail page, and `visits.geojson`
  prefer this position; if it is missing, the source coordinates still apply.
  The source coordinates remain in `lat`/`lng`.
- OSM data is licensed under ODbL; public display requires
  "© OpenStreetMap contributors". Nominatim is not a bulk scraper — keep the
  cache and rate limit in place.

## 🟦 Foursquare details

- Check-ins: 2339 per API, 2338 unique (1 deleted/private)
- 1036 venues, 577 photos (562 linked to check-ins, 15 standalone),
  115 comments, 294 likes
- Period: 2010-01-28 to 2026-09-01
- Pagination via `beforeTimestamp` (offset paging is capped by Foursquare after
  a few hundred entries and then repeats the first page)
- User photos return at most 200 entries per page; the offset must be advanced
  by the number actually returned. The 577 API items contain 574 unique IDs
  (3 duplicates).
- Comment texts are loaded via `/v2/checkins/<id>/comments`
- The sync state (`last_max_created_at`) is stored in `sync_state`; `sync` only
  fetches new items and rewrites the exports
- Also imported: `venuehistory` (1029 of 1051 API venues), `lists` (5 lists,
  150 list entries; 53 of 54 for "My Liked Places"), `tips` (11, including
  details such as language, saves, and title), and all user photos (577; 562 with
  check-in, 15 standalone)
- The missing entries (22 venues, 1 list entry) are not delivered even with
  alternative offset strategies and are documented in `sync_diagnostics`.
- Tastes (`/v2/users/self/tastes`, 17 entries) and tip details (`/v2/tips/<id>`)
  are stored completely, including raw JSON
- User profile including counters and a locally stored avatar
  (`data/foursquare/profile/`)
- Known API gaps are documented in `sync_diagnostics` and shown in `stats`
- Venue details (opening hours, price, rating, venue photos/tips) are currently
  unavailable: `/v2/venues/{id}` returns 402, the Places API with premium
  fields, `/photos`, `/tips`, and `/places/ask` return 429. The 1036 venue
  snapshots from check-ins are sufficient for the list and detail page.
- The Places API (`https://places-api.foursquare.com`,
  `FOURSQUARE_SERVICE_API_KEY`) does provide baseline details (200) for **all
  1036 venues**: current name, address, city/region/postal code/country,
  coordinates, categories, phone (747), website (748), email (395), and social
  media IDs. The client lives in `places_api.py`.
- The baseline was saved once to `data/foursquare/places_api_venue_details.json`
  and imported into the `venue_details` table with `foursquare venue-import`
  (1036 entries, 811 with contact data). The import is idempotent, makes no API
  calls, and does not overwrite existing venue snapshots.
- The `/venues/{id}` detail page shows phone, website, email, social links, all
  categories, and the data timestamp from it; empty fields are hidden.
- Quota note: the one-time fetch of all 1036 venues exhausted the freePro quota;
  further detail calls return 429 until the reset/credits. Importing from JSON
  consumes no quota.
- `places/search`, `autocomplete`, `places/match`, and `geotagging/candidates`
  respond with 200, `places/resolve` with 401 (missing permission).
- `foursquare probe` checks the account endpoints read-only; `friends`/`badges`
  return 404, `events` 403, `likes`/`herenow` 402. It also checks the Places API
  baseline, premium fields, photos, and tips.

## 🟥 Yelp import

```bash
python3 -m spot_history yelp import
python3 -m spot_history index build       # Foursquare index for name matching
python3 -m spot_history yelp geocode      # Nominatim, max. 1 request/s
python3 -m spot_history index build       # copy cities into the index
python3 -m spot_history yelp stats
```

The HTML export is referenced via `YELP_EXPORT_DIR` (or `--export-dir`).
Without it, `data/yelp/export` relative to the repository is used.

- 462 check-ins, 284 places, 92 with source coordinates, 270 media files
- 20 comments without a check-in are imported as additional visits (badge
  `Kommentar`); 8 already linked comments appear on the respective check-in
  visit
- Also imported: 39 survey answers, 45 business interactions, 36 review
  feedback entries, 8 photo feedback entries, 143 compliments, 25 messages,
  3 milestones, 2 forum posts, 1 profile question, and the account data
  (10 fields; sensitive fields are not shown in the web UI)
- 17 source tables without data (including reservations, waitlists, tips) are
  documented with headers and row counts in `source_tables`
- 16 Yelp places have a business URL from bookmarks/collections; it is stored in
  `places.place_url` during import and carried into the index as a source link
- The Yelp `Datum` is UTC; the export contains no place offset. In `yelp.db`,
  `tz_offset` therefore remains `NULL`. The index resolves local time (same
  place, place time zone, city/country, neighborhood majority, or a nearby
  Foursquare check-in); currently all Yelp visits (482 including 20 comment
  visits) are resolved.
- 306 Yelp map points (all 482 Yelp visits including comment visits): 92 with
  source coordinates, 214 derived from the place (badge `Ort abgeleitet` in the
  list and map)
- 316 of 462 check-in visits with city: 135 via Nominatim, 181 via exact name
  matches against the Foursquare index and export URLs
- 146 check-in visits remain without a city, 176 Yelp visits without a map point
- The geocoding cache lives under `data/yelp/` and is gitignored; `--force`
  forces new Nominatim requests
- Review IPs are imported; account/login IPs and their files deliberately are
  not. `--no-media` only sets `local_path` when the target file exists.
- Remaining unlinked: 327 check-ins without coordinates, 111 places/146 visits
  without a city, and 20 comments and 29 media items. Business IDs, addresses,
  categories, and business URLs are missing from the export.

## 🟩 Google Timeline import

Google Timeline has only existed on-device since the 2024/25 migration. There is
no API and no web export; Google Takeout only contains the legacy data from
before the migration. The import is therefore two-stage: a manual export from
the Google Maps app, followed by automatic import and OSM enrichment.

### 📱 Export from iPhone

1. Google Maps → Profile → Settings → Location & privacy → Export Timeline
   data.
2. In the iOS share sheet, choose "Save to Files"; the file is called
   `location-history.json` (or `Timeline.json`).
3. Save it to a folder that syncs to Linux (Dropbox, Nextcloud, Google Drive +
   `rclone`; iCloud Drive cannot be mounted directly).
4. Turn off auto-delete in Timeline or set it to at least 36 months; otherwise
   data disappears before the next export.

Set the path in `.env`:

```env
GOOGLE_TIMELINE_INBOX=/path/to/google-timeline
```

The importer detects files by content, not by name. It supports the iPhone array
export, `semanticSegments` (Android/Phone), legacy Takeout
(`timelineObjects`/`placeVisit`), and GeoJSON from reviews/saved places.
`Records.json` (raw GPS pings) and `@eaDir` are skipped.

### 📥 Import and OSM enrichment

```bash
python3 -m spot_history google scan
python3 -m spot_history google import
python3 -m spot_history google osm
python3 -m spot_history google stats
```

- `google import` rebuilds `db/google/google.db` from all files in the inbox
  folder on every run. Duplicate visits are merged via stable IDs; Google place
  IDs with identical coordinates (≤15 m) are consolidated into one place.
- `Home` and `Work` are shown as `Zuhause`/`Arbeit`; the addresses of these
  places are not displayed.
- `google osm` first uses the existing cache in `db/osm/osm.db` (read-only),
  then Overpass (`nwr(around:55)[name]`) and Nominatim reverse as a fallback.
  Results are stored in `db/google/osm_cache.db`.
- Only unambiguous matches ≤25 m are accepted automatically. Matches between
  25 and 50 m and ambiguous results become candidates in `google osm-candidates`;
  they can be accepted there with `google osm-accept`.
- Nominatim reverse matches with a valid name and at most 50 m distance also
  adopt `osm_type` and `osm_id`. These clusters are then treated as OSM places in
  the index and merged with similarly named sources. Matches over 50 m still
  only provide the name; address matches receive no OSM ID. Already processed
  address clusters are not queried again without `--force`.
- Reviews and saved places are imported as `source_records`, shown on venue
  detail pages, and used within 15 m as a name hint (a Google review can replace
  an inaccurate OSM name). The Google tab `/google` shows all reviews and saved
  places, regardless of whether they could be assigned to a place.
- Manual names: `google name <cluster_key> "Name"` (optionally with `--address`,
  `--city`, `--category`). Overrides survive every re-import.
- No paid APIs are used. Overpass/Nominatim are used with cache, batch size, and
  pauses within fair-use limits; OSM data is licensed under ODbL
  ("© OpenStreetMap contributors").
- The Google Maps link remains available as `place_url`
  (`...?api=1&query_place_id=...`) even when no name was found.

Known limits: Google provides only a place ID + coordinates, no names. There is
no Google ID → OSM ID mapping; matching is based on coordinates. In dense areas,
the nearest OSM POI can be wrong, so 25–50 m matches are only stored as
candidates. Many places receive an address (street + house number) instead of a
venue name. The iPhone export itself remains manual; `google.sh` handles import,
OSM enrichment, and index rebuild.

## 🧩 More sources

See [`CONNECTORS.md`](CONNECTORS.md) for the connector contract. Connectors are
auto-discovered under `spot_history/connectors/*` and must export `CONNECTOR`
there.

## 🧪 Tests

```bash
python3 -m unittest discover -s tests
```

## 🤖 CI/CD

- `.github/workflows/test.yml` runs the unit tests on pull requests and
  non-`main` branches.
- `.github/workflows/build.yml` runs the tests, builds a `linux/amd64` image, and
  pushes `ghcr.io/skoelle/spot-history-manager:latest`. The image is rebuilt
  weekly without cache; manual runs can request a cache-free build.
- Optional deployment webhook: set the repository secrets `WEBHOOK_URL` and
  `WEBHOOK_TOKEN`. If `WEBHOOK_URL` is set, the workflow sends a `POST` with
  `Authorization: Bearer <token>` and expects HTTP 200 plus
  `summary.updated > 0` in the JSON response.
- The GHCR package is private by default. Either make it public in the package
  settings or log the deploy host in to `ghcr.io` before pulling.

## 📝 Notes

- `.env`, `data/`, and `db/` are gitignored and must not be committed.
- The raw JSON also contains names of users who liked check-ins.
- Photos are loaded at original resolution; `--no-photos` skips this.
- The Google Timeline export remains manual (iOS share sheet); `google.sh` then
  handles import, OSM enrichment, and index rebuild.
- No app-internal cron/daemon: sync and index builds are started manually via
  the CLI or the Sync page. Only the SQLite backup is meant to run from a host
  systemd timer or cron job.

## 📄 License

Licensed under the [MIT License](LICENSE) - Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
