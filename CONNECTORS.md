# Connector contract

This repository collects data from multiple services. Each source is its own
connector with its own SQLite database. A separate index aggregates all sources
for the map. Connectors must not modify each other.

## Rules

- A connector lives exclusively under `spot_history/connectors/<name>/`.
- Shared files (`core/`, `cli.py`, `connectors/base.py`, `CONNECTORS.md`) are
  changed only when necessary for all connectors — not for source-specific
  special cases.
- Own database under `db/<name>/<name>.db`; no writes to other connectors or
  to `db/index/`.
- `db/places/place_rules.db` is cross-source and is written only by
  `spot_history places ...`, not by connectors.
- No secrets, raw data, or media in Git. `data/`, `db/`, and `.env` are ignored.
- The index is built from the connectors and is disposable. Source databases
  are the source of truth.

## Auto-discovery

`spot_history/connectors/__init__.py` scans all subpackages and reads
`CONNECTOR`. Therefore `spot_history/connectors/<name>/__init__.py` must export
the connector:

```python
from .connector import CONNECTOR

__all__ = ["CONNECTOR"]
```

`CONNECTOR` is an instance that inherits from `BaseConnector`.

## Required interface

`spot_history/connectors/base.py`:

```python
class BaseConnector:
    name: str          # stable source name, e.g. "yelp"
    kind: str          # "sync" or "import"

    def add_parser(self, subparsers) -> None: ...
    def run(self, args) -> int: ...
    def iter_visits(self) -> Iterable[Visit]: ...
    def iter_records(self) -> Iterable[dict]: ...  # optional
```

- `add_parser`: adds `python -m spot_history <name> <command>` to the CLI tree.
  Set `func` to the connector's own `run` method for each command.
- `run`: performs sync/import/export/stats and returns an exit code.
- `iter_visits`: reads the connector's own database **read-only** and returns
  canonical visits for the index. Return an empty sequence when there is no data
  (no error).
- `iter_records` (optional): returns detail or activity data for the index and
  the web UI. Each record contains `source`, `record_id`, `kind`, optionally
  `source_place_id` and `created_at`, plus a JSON-serializable `payload`.
  Sensitive fields must not be included in these records if they should not
  appear in the web UI.

## Canonical visit format

`Visit` (see `connectors/base.py`):

| Field | Required | Meaning |
|---|---|---|
| `source` | yes | Connector name |
| `source_visit_id` | yes | Stable ID of the visit in the source |
| `visited_at` | yes | Unix time (UTC) |
| `lat`, `lng` | no | Coordinates; without them there is no map point |
| `place_name` | no | Place name |
| `source_place_id` | no | Stable place ID of the source |
| `category` | no | Category/subcategory |
| `address`, `city`, `state`, `postal_code`, `country_code` | no | Address |
| `note` | no | Comment/note |
| `source_url` | no | Link to the visit/original |
| `place_url` | no | Link to the place page in the source (e.g. venue/business) |
| `tz_offset` | no | Offset in minutes from UTC; `None` if the source does not provide it |
| `time_zone` | no | IANA time zone (e.g. `Europe/Berlin`) for resolution |
| `photos` | no | List of `Photo` |
| `extra` | no | Source-specific extra fields (JSON) |

`Photo`:

| Field | Required | Meaning |
|---|---|---|
| `source_photo_id` | yes | Stable photo ID of the source |
| `url` | no | Original URL |
| `local_path` | no | Path relative to the repo root, e.g. `data/yelp/media/x.jpg` |
| `taken_at` | no | Unix time |

## Import vs. sync connectors

- `kind="sync"`: regularly updatable (API), with its own sync state in the
  database.
- `kind="import"`: one-time import from an export (e.g. Yelp, Google Timeline).
  Still write to a dedicated SQLite database so the index and later queries are
  fast.

An import may be repeatable: same IDs via upsert, no duplicates.

## Index and canonical places

`python3 -m spot_history index build`:

1. calls `iter_visits()` and optionally `iter_records()` for each connector,
2. forms source-specific places from `source` + `source_place_id`,
3. merges places conservatively by normalized name, coordinates (≤ 50 m), or
   name + unique city; manual rules from `db/places/place_rules.db` take
   precedence,
4. resolves missing `tz_offset` values (same place, place time zone,
   city/country, neighborhood majority, or nearby Foursquare check-in),
5. rewrites `db/index/index.db` (`canonical_places`, `place_links`,
   `merge_candidates`, visits with `canonical_place_id`),
6. writes `data/exports/visits.geojson` (points with `[lng, lat]`).

The index contains canonical fields plus optional detail records
(`source_records`) for the web UI. Raw data and sensitive special cases remain
in the source database. The rules database is persistent and does not belong in
the disposable index. New connectors do not require changes to the index code as
long as they provide a stable `source_place_id` and — if available — `place_url`.

## Tests and acceptance

- Test parsers/imports with small fixtures (`tests/test_<name>.py`), with no
  dependency on the network or the NAS export.
- `python3 -m unittest discover -s tests` must pass.
- `python3 -m spot_history index build` must show the new connector in `sources`
  and in `visits.geojson`.
- No secrets in `git status`; `.env`, `data/`, and `db/` remain untracked.

## Checklist for a new connector

- [ ] `spot_history/connectors/<name>/__init__.py` exports `CONNECTOR`
- [ ] `BaseConnector` implemented (`add_parser`, `run`, `iter_visits`)
- [ ] own database under `db/<name>/<name>.db`
- [ ] canonical visits with stable IDs
- [ ] media (if present) under `data/<name>/media/`
- [ ] tests with fixtures
- [ ] `index build` verified
- [ ] no changes to other connectors
