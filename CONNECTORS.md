# Connector-Vertrag

Dieses Repository sammelt Daten mehrerer Dienste. Jede Quelle ist ein eigener
Connector mit eigener SQLite-DB. Ein separater Index fasst alle Quellen für die
Karte zusammen. Connectoren dürfen sich gegenseitig nicht verändern.

## Regeln

- Ein Connector lebt ausschließlich unter `spot_history/connectors/<name>/`.
- Gemeinsame Dateien (`core/`, `cli.py`, `connectors/base.py`, `CONNECTORS.md`)
  werden nur geändert, wenn es für alle Connectoren nötig ist – nicht für
  quellenspezifische Sonderfälle.
- Eigene DB unter `data/<name>/<name>.db`; keine Schreibzugriffe auf andere
  Connectoren oder auf `data/index/`.
- `data/places/place_rules.db` ist quellenübergreifend und wird nur von
  `spot_history places ...` geschrieben, nicht von Connectoren.
- Keine Secrets, Rohdaten oder Medien ins Git. `data/` und `.env` sind ignored.
- Der Index wird aus den Connectoren gebaut und ist wegwerfbar. Quell-DBs sind
  die Wahrheit.

## Auto-Discovery

`spot_history/connectors/__init__.py` durchsucht alle Unterpakete und liest
`CONNECTOR`. Deshalb muss `spot_history/connectors/<name>/__init__.py` den
Connector exportieren:

```python
from .connector import CONNECTOR

__all__ = ["CONNECTOR"]
```

`CONNECTOR` ist eine Instanz, die von `BaseConnector` erbt.

## Pflicht-Interface

`spot_history/connectors/base.py`:

```python
class BaseConnector:
    name: str          # stabiler Quellenname, z. B. "yelp"
    kind: str          # "sync" oder "import"

    def add_parser(self, subparsers) -> None: ...
    def run(self, args) -> int: ...
    def iter_visits(self) -> Iterable[Visit]: ...
```

- `add_parser`: hängt `python -m spot_history <name> <command>` in den CLI-Baum.
  Für jeden Command `func` auf die eigene `run`-Methode setzen.
- `run`: führt Sync/Import/Export/Stats aus und gibt einen Exit-Code zurück.
- `iter_visits`: liest die eigene DB **nur lesend** und liefert kanonische
  Visits für den Index. Ohne Daten eine leere Sequenz liefern (kein Fehler).

## Kanonisches Visit-Format

`Visit` (siehe `connectors/base.py`):

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `source` | ja | Connector-Name |
| `source_visit_id` | ja | stabile ID des Besuchs in der Quelle |
| `visited_at` | ja | Unix-Zeit (UTC) |
| `lat`, `lng` | nein | Koordinaten; ohne sie kein Kartenpunkt |
| `place_name` | nein | Ortsname |
| `source_place_id` | nein | stabile Orts-ID der Quelle |
| `category` | nein | Kategorie/Unterkategorie |
| `address`, `city`, `state`, `postal_code`, `country_code` | nein | Adresse |
| `note` | nein | Kommentar/Notiz |
| `source_url` | nein | Link zum Besuch/Original |
| `place_url` | nein | Link zur Ortsseite in der Quelle (z. B. Venue/Business) |
| `tz_offset` | nein | Minuten Offset zu UTC |
| `photos` | nein | Liste von `Photo` |
| `extra` | nein | quellenspezifische Zusatzfelder (JSON) |

`Photo`:

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `source_photo_id` | ja | stabile Foto-ID der Quelle |
| `url` | nein | Original-URL |
| `local_path` | nein | Pfad relativ zum Repo-Root, z. B. `data/yelp/media/x.jpg` |
| `taken_at` | nein | Unix-Zeit |

## Import- vs. Sync-Connectoren

- `kind="sync"`: regelmäßig aktualisierbar (API), eigener Sync-Stand in der DB.
- `kind="import"`: einmaliger Import aus einem Export (z. B. Yelp, Google
  Timeline). Trotzdem in eine eigene SQLite-DB schreiben, damit der Index und
  spätere Abfragen schnell sind.

Ein Import darf wiederholbar sein: gleiche IDs per Upsert, keine Duplikate.

## Index und kanonische Orte

`python3 -m spot_history index build`:

1. ruft für jeden Connector `iter_visits()` auf,
2. bildet aus `source` + `source_place_id` quellenspezifische Orte,
3. führt Orte konservativ über normalisierten Namen, Koordinaten (≤ 50 m) oder
   Name + eindeutige Stadt zusammen; manuelle Regeln aus
   `data/places/place_rules.db` haben Vorrang,
4. schreibt `data/index/index.db` neu (`canonical_places`, `place_links`,
   `merge_candidates`, Visits mit `canonical_place_id`),
5. schreibt `data/exports/visits.geojson` (Punkte mit `[lng, lat]`).

Der Index enthält nur kanonische Felder. Rohdaten und Sonderfälle bleiben in der
Quell-DB. Die Regel-DB ist persistent und gehört nicht in den wegwerfbaren
Index. Neue Connectoren brauchen keine Änderung am Index-Code, solange sie
stabile `source_place_id` und – wenn vorhanden – `place_url` liefern.

## Tests und Abnahme

- Parser/Import mit kleinen Fixtures testen (`tests/test_<name>.py`), keine
  Abhängigkeit von Netzwerk oder dem NAS-Export.
- `python3 -m unittest discover -s tests` muss grün sein.
- `python3 -m spot_history index build` muss den neuen Connector in
  `sources` und in `visits.geojson` zeigen.
- Keine Secrets in `git status`; `.env` und `data/` bleiben ungetrackt.

## Checkliste für einen neuen Connector

- [ ] `spot_history/connectors/<name>/__init__.py` exportiert `CONNECTOR`
- [ ] `BaseConnector` implementiert (`add_parser`, `run`, `iter_visits`)
- [ ] eigene DB unter `data/<name>/<name>.db`
- [ ] kanonische Visits mit stabilen IDs
- [ ] Medien (falls vorhanden) unter `data/<name>/media/`
- [ ] Tests mit Fixtures
- [ ] `index build` geprüft
- [ ] keine Änderungen an anderen Connectoren
