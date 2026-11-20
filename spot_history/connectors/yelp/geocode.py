from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

from ...core.config import ROOT
from .db import Database, iso_now
from .ids import normalize_name

NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
DEFAULT_USER_AGENT = os.environ.get(
    "YELP_GEOCODER_USER_AGENT", "spot-history-manager/0.1 (local import)"
)


@dataclass
class GeocodeResult:
    city: str | None = None
    state: str | None = None
    country_code: str | None = None

    @property
    def has_values(self) -> bool:
        return bool(self.city or self.state or self.country_code)


@dataclass
class GeocodeSummary:
    reverse_ok: int = 0
    reverse_cached: int = 0
    reverse_not_found: int = 0
    reverse_errors: int = 0
    foursquare_ok: int = 0
    foursquare_ambiguous: int = 0
    url_ok: int = 0
    url_conflict: int = 0
    url_ambiguous: int = 0
    places_with_city: int = 0
    visits_with_city: int = 0
    unknown_places: int = 0
    unknown_visits: int = 0
    warnings: list[str] = field(default_factory=list)


class NominatimGeocoder:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = 20.0,
        retries: int = 3,
        language: str = "de",
    ) -> None:
        self.user_agent = user_agent
        self.timeout = timeout
        self.retries = retries
        self.language = language

    def reverse(self, lat: float, lng: float) -> GeocodeResult | None:
        params = urlencode(
            {
                "format": "jsonv2",
                "lat": f"{lat:.7f}",
                "lon": f"{lng:.7f}",
                "zoom": 10,
                "addressdetails": 1,
                "accept-language": self.language,
            }
        )
        request = Request(
            f"{NOMINATIM_REVERSE}?{params}",
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
        )
        attempt = 0
        while True:
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                break
            except HTTPError as exc:
                if exc.code == 429 or 500 <= exc.code < 600:
                    if attempt >= self.retries:
                        raise
                    retry_after = exc.headers.get("Retry-After")
                    delay = 2.0 * (attempt + 1)
                    if retry_after:
                        try:
                            delay = max(delay, float(retry_after))
                        except ValueError:
                            pass
                    time.sleep(delay)
                    attempt += 1
                    continue
                raise
            except (URLError, TimeoutError):
                if attempt >= self.retries:
                    raise
                time.sleep(2.0 * (attempt + 1))
                attempt += 1
                continue

        address = payload.get("address") or {}
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("city_district")
        )
        state = address.get("state")
        country_code = address.get("country_code")
        result = GeocodeResult(
            city=city,
            state=state,
            country_code=country_code.upper() if country_code else None,
        )
        return result if result.has_values else None


def _slug(value: str) -> str:
    text = (
        value.replace("ß", "ss")
        .replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
    )
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _foursquare_venues(
    index_db: Path | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if index_db and Path(index_db).exists():
        conn = sqlite3.connect(f"file:{index_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for row in conn.execute(
                "SELECT name, city, state, country_code FROM places "
                "WHERE source='foursquare' AND city IS NOT NULL AND city != ''"
            ):
                rows.append(dict(row))
        finally:
            conn.close()
        return rows
    fallback = ROOT / "data" / "foursquare" / "foursquare.db"
    if fallback.exists():
        conn = sqlite3.connect(f"file:{fallback}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        try:
            for row in conn.execute(
                "SELECT name, city, state, country_code FROM venues "
                "WHERE city IS NOT NULL AND city != ''"
            ):
                rows.append(dict(row))
        finally:
            conn.close()
    return rows


def _city_infos(
    venues: list[dict[str, Any]],
    cache_rows: list[sqlite3.Row],
) -> dict[str, tuple[str | None, str | None]]:
    infos: dict[str, set[tuple[str | None, str | None]]] = {}
    for row in venues:
        infos.setdefault(row["city"], set()).add((row["state"], row["country_code"]))
    for row in cache_rows:
        if row["status"] == "ok" and row["city"]:
            infos.setdefault(row["city"], set()).add((row["state"], row["country_code"]))
    result: dict[str, tuple[str | None, str | None]] = {}
    for city, values in infos.items():
        result[city] = next(iter(values)) if len(values) == 1 else (None, None)
    return result


def _url_cities(
    urls: list[str],
    city_slugs: dict[str, set[str]],
) -> set[str]:
    found: set[str] = set()
    for url in urls:
        path = _slug(unquote(urlsplit(url).path))
        padded = f"-{path}-"
        for city_slug, cities in city_slugs.items():
            if f"-{city_slug}-" in padded:
                found.update(cities)
    return found


def _url_index(db: Database) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for row in db.conn.execute(
        "SELECT place_name, place_url FROM bookmarks WHERE place_url IS NOT NULL"
    ):
        result.setdefault(normalize_name(row["place_name"]), []).append(row["place_url"])
    for row in db.conn.execute(
        "SELECT place_name, place_url FROM collection_items WHERE place_url IS NOT NULL"
    ):
        result.setdefault(normalize_name(row["place_name"]), []).append(row["place_url"])
    return result


def _reverse_geocode(
    db: Database,
    geocoder: Any,
    summary: GeocodeSummary,
    force: bool,
    sleep: float,
    limit: int | None,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    places = db.places_with_coords(limit)
    for index, place in enumerate(places):
        cache = db.geocode_cache(place["place_key"])
        same_coords = (
            cache is not None
            and cache["lat"] is not None
            and cache["lng"] is not None
            and round(float(cache["lat"]), 5) == round(float(place["lat"]), 5)
            and round(float(cache["lng"]), 5) == round(float(place["lng"]), 5)
        )
        if not force and same_coords and cache["status"] in ("ok", "not_found"):
            summary.reverse_cached += 1
            continue
        if dry_run:
            summary.reverse_ok += 1
            continue
        try:
            result = geocoder.reverse(place["lat"], place["lng"])
        except Exception as exc:  # noqa: BLE001 - external service
            summary.reverse_errors += 1
            summary.warnings.append(f"Geocoding fehlgeschlagen für {place['name']}: {exc}")
            log(f"Warnung: Geocoding fehlgeschlagen für {place['name']}: {exc}")
            continue
        status = "ok" if result and result.has_values else "not_found"
        if status == "ok":
            summary.reverse_ok += 1
        else:
            summary.reverse_not_found += 1
        db.upsert_geocodes(
            [
                {
                    "place_key": place["place_key"],
                    "lat": place["lat"],
                    "lng": place["lng"],
                    "city": result.city if result else None,
                    "state": result.state if result else None,
                    "country_code": result.country_code if result else None,
                    "source": "nominatim",
                    "status": status,
                    "error": None,
                    "fetched_at": iso_now(),
                }
            ]
        )
        if index < len(places) - 1 and sleep > 0:
            time.sleep(sleep)


def _name_matches(
    db: Database,
    index_db: Path | None,
    summary: GeocodeSummary,
    dry_run: bool,
    log: Callable[[str], None],
) -> None:
    venues = _foursquare_venues(index_db)
    if not venues:
        summary.warnings.append(
            "Foursquare-Index/DB nicht gefunden; Namensmatches übersprungen"
        )
        return
    by_name: dict[str, list[dict[str, Any]]] = {}
    for venue in venues:
        by_name.setdefault(normalize_name(venue["name"]), []).append(venue)

    cache_rows = db.conn.execute(
        "SELECT * FROM place_geocodes WHERE status = 'ok'"
    ).fetchall()
    city_infos = _city_infos(venues, cache_rows)
    city_slugs: dict[str, set[str]] = {}
    for city in city_infos:
        slug = _slug(city)
        if len(slug) >= 3:
            city_slugs.setdefault(slug, set()).add(city)

    urls = _url_index(db)
    no_coords = db.places_without_coords()
    if not dry_run and no_coords:
        with db.conn:
            db.conn.executemany(
                "DELETE FROM place_geocodes WHERE place_key = ?",
                [(place["place_key"],) for place in no_coords],
            )

    for place in no_coords:
        name = place["name"]
        fs_candidates = by_name.get(normalize_name(name), [])
        fs_city = None
        if len(fs_candidates) == 1:
            fs_city = fs_candidates[0]["city"]
        elif len(fs_candidates) > 1:
            summary.foursquare_ambiguous += 1
            if not dry_run:
                db.upsert_geocodes(
                    [
                        {
                            "place_key": place["place_key"],
                            "lat": None,
                            "lng": None,
                            "city": None,
                            "state": None,
                            "country_code": None,
                            "source": "foursquare_index",
                            "status": "ambiguous",
                            "error": "mehrere Foursquare-Venues mit gleichem Namen",
                            "fetched_at": iso_now(),
                        }
                    ]
                )
            continue

        url_found = _url_cities(urls.get(normalize_name(name), []), city_slugs)
        url_city = next(iter(url_found)) if len(url_found) == 1 else None
        if len(url_found) > 1:
            summary.url_ambiguous += 1

        if fs_city and url_city and fs_city.casefold() != url_city.casefold():
            summary.url_conflict += 1
            summary.warnings.append(
                f"Stadtkonflikt für {name}: Foursquare={fs_city}, URL={url_city}"
            )
            log(f"Warnung: Stadtkonflikt für {name}: {fs_city} vs. {url_city}")
            if not dry_run:
                db.upsert_geocodes(
                    [
                        {
                            "place_key": place["place_key"],
                            "lat": None,
                            "lng": None,
                            "city": None,
                            "state": None,
                            "country_code": None,
                            "source": "foursquare_index+export_url",
                            "status": "conflict",
                            "error": f"Foursquare={fs_city}; URL={url_city}",
                            "fetched_at": iso_now(),
                        }
                    ]
                )
            continue

        if fs_city:
            city = fs_city
            state, country_code = city_infos.get(city, (None, None))
            source = "foursquare_index"
            if url_city and url_city.casefold() == fs_city.casefold():
                source = "foursquare_index+export_url"
            summary.foursquare_ok += 1
        elif url_city:
            city = url_city
            state, country_code = city_infos.get(city, (None, None))
            source = "export_url"
            summary.url_ok += 1
        else:
            continue

        if not dry_run:
            db.upsert_geocodes(
                [
                    {
                        "place_key": place["place_key"],
                        "lat": None,
                        "lng": None,
                        "city": city,
                        "state": state,
                        "country_code": country_code,
                        "source": source,
                        "status": "ok",
                        "error": None,
                        "fetched_at": iso_now(),
                    }
                ]
            )


def geocode_places(
    db: Database,
    geocoder: Any | None = None,
    index_db: str | Path | None = None,
    force: bool = False,
    sleep: float = 1.0,
    limit: int | None = None,
    dry_run: bool = False,
    reverse: bool = True,
    name_match: bool = True,
    log: Callable[[str], None] = print,
) -> GeocodeSummary:
    summary = GeocodeSummary()
    geocoder = geocoder or NominatimGeocoder()
    index_path = Path(index_db) if index_db else ROOT / "data" / "index" / "index.db"

    if reverse:
        _reverse_geocode(
            db, geocoder, summary, force, sleep, limit, dry_run, log
        )
    if name_match:
        _name_matches(db, index_path, summary, dry_run, log)

    if not dry_run:
        db.rebuild_places()
        stats = db.stats()
        summary.places_with_city = stats["places_with_city"]
        summary.visits_with_city = stats["visits_with_city"]
        summary.unknown_places = stats["places"] - summary.places_with_city
        summary.unknown_visits = stats["checkins"] - summary.visits_with_city
    return summary
