# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from .db import Poi

DEFAULT_OVERPASS_ENDPOINTS = (
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
USER_AGENT = "spot-history-manager/1.0 (personal OSM enrichment)"


class OverpassError(RuntimeError):
    pass


class NominatimError(RuntimeError):
    pass


def _post(endpoint: str, query: str, timeout: float) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


def parse_overpass_elements(payload: dict) -> list[Poi]:
    pois: list[Poi] = []
    seen: set[tuple[str, int]] = set()
    for element in payload.get("elements") or []:
        tags = element.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        osm_type = element.get("type")
        osm_id = element.get("id")
        if osm_type not in ("node", "way", "relation") or osm_id is None:
            continue
        center = element.get("center") or {}
        lat = element.get("lat", center.get("lat"))
        lng = element.get("lon", center.get("lon"))
        key = (osm_type, int(osm_id))
        if key in seen:
            continue
        seen.add(key)
        pois.append(
            Poi(
                osm_type=osm_type,
                osm_id=int(osm_id),
                name=name,
                lat=float(lat) if lat is not None else None,
                lng=float(lng) if lng is not None else None,
                tags={key: value for key, value in tags.items() if value is not None},
            )
        )
    return pois


class OverpassClient:
    def __init__(
        self,
        endpoints: tuple[str, ...] = DEFAULT_OVERPASS_ENDPOINTS,
        timeout: float = 90.0,
        sleep: float = 2.0,
        verbose: bool = False,
    ) -> None:
        self.endpoints = endpoints
        self.timeout = timeout
        self.sleep = sleep
        self.verbose = verbose
        self._endpoint_index = 0

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def fetch_around(
        self, points: list[tuple[float, float]], radius: int
    ) -> list[Poi]:
        if not points:
            return []
        clauses = "\n".join(
            f"  nwr(around:{radius},{lat:.6f},{lng:.6f})[name];"
            for lat, lng in points
        )
        query = (
            f"[out:json][timeout:{int(self.timeout)}];\n"
            f"(\n{clauses}\n);\n"
            "out tags center;"
        )
        last_error: Exception | None = None
        for attempt in range(len(self.endpoints) * 2):
            endpoint = self.endpoints[self._endpoint_index % len(self.endpoints)]
            self._endpoint_index += 1
            started = time.time()
            try:
                payload = _post(endpoint, query, self.timeout)
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")[:200]
                last_error = OverpassError(f"{exc.code} {body}")
                self._log(f"Overpass {endpoint}: HTTP {exc.code}, nächster Endpoint")
                time.sleep(min(2 ** attempt, 20))
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                self._log(f"Overpass {endpoint}: {exc}, nächster Endpoint")
                time.sleep(min(2 ** attempt, 20))
                continue
            pois = parse_overpass_elements(payload)
            if endpoint != self.endpoints[0]:
                self.endpoints = (endpoint,) + tuple(
                    item for item in self.endpoints if item != endpoint
                )
            self._endpoint_index = 0
            self._log(
                f"Overpass {len(points)} Punkte -> {len(pois)} POIs "
                f"({time.time() - started:.1f}s, {endpoint})"
            )
            time.sleep(self.sleep)
            return pois
        raise OverpassError(
            f"Overpass nach {len(self.endpoints) * 2} Versuchen fehlgeschlagen: "
            f"{last_error}"
        )


def parse_nominatim_results(payload: list[dict]) -> list[Poi]:
    pois: list[Poi] = []
    for item in payload:
        osm_type = item.get("osm_type")
        osm_id = item.get("osm_id")
        if osm_type not in ("node", "way", "relation") or osm_id is None:
            continue
        extratags = item.get("extratags") or {}
        address = item.get("address") or {}
        tags = {
            str(key): str(value)
            for key, value in extratags.items()
            if value is not None
        }
        category = item.get("category")
        osm_class = item.get("type")
        if category and osm_class:
            tags.setdefault(str(category), str(osm_class))
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("suburb")
        )
        if city:
            tags.setdefault("addr:city", str(city))
        postcode = address.get("postcode")
        if postcode:
            tags.setdefault("addr:postcode", str(postcode))
        road = address.get("road")
        if road:
            tags.setdefault("addr:street", str(road))
        house_number = address.get("house_number")
        if house_number:
            tags.setdefault("addr:housenumber", str(house_number))
        name = (
            (item.get("namedetails") or {}).get("name")
            or item.get("name")
            or tags.get("name")
            or (item.get("display_name") or "").split(",")[0]
        )
        if not name:
            continue
        lat = item.get("lat")
        lng = item.get("lon")
        pois.append(
            Poi(
                osm_type=osm_type,
                osm_id=int(osm_id),
                name=str(name),
                lat=float(lat) if lat is not None else None,
                lng=float(lng) if lng is not None else None,
                tags=tags,
            )
        )
    return pois


class NominatimClient:
    def __init__(
        self,
        url: str = NOMINATIM_URL,
        timeout: float = 30.0,
        sleep: float = 1.1,
        verbose: bool = False,
    ) -> None:
        self.url = url
        self.timeout = timeout
        self.sleep = sleep
        self.verbose = verbose

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def search_raw(self, query: str, limit: int = 5) -> list[dict]:
        params = urllib.parse.urlencode(
            {
                "q": query,
                "format": "jsonv2",
                "addressdetails": 1,
                "extratags": 1,
                "namedetails": 1,
                "limit": limit,
            }
        )
        request = urllib.request.Request(
            f"{self.url}?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NominatimError(f"Nominatim-Anfrage fehlgeschlagen: {exc}") from exc
        finally:
            time.sleep(self.sleep)
        if not isinstance(payload, list):
            raise NominatimError("Nominatim lieferte kein JSON-Array")
        self._log(f"Nominatim {query!r} -> {len(payload)} Treffer")
        return payload

    def reverse_raw(self, lat: float, lng: float, zoom: int = 18) -> dict:
        params = urllib.parse.urlencode(
            {
                "lat": f"{lat:.6f}",
                "lon": f"{lng:.6f}",
                "format": "jsonv2",
                "zoom": zoom,
                "addressdetails": 1,
                "extratags": 1,
                "namedetails": 1,
            }
        )
        request = urllib.request.Request(
            f"{NOMINATIM_REVERSE_URL}?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise NominatimError(f"Nominatim-Reverse fehlgeschlagen: {exc}") from exc
        finally:
            time.sleep(self.sleep)
        if not isinstance(payload, dict):
            raise NominatimError("Nominatim-Reverse lieferte kein JSON-Objekt")
        self._log(
            f"Nominatim-Reverse {lat:.5f},{lng:.5f} -> "
            f"{payload.get('name') or payload.get('display_name')!r}"
        )
        return payload
