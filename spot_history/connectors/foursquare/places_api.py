# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in project root for details.
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

DEFAULT_BASE_URL = "https://places-api.foursquare.com"
DEFAULT_API_VERSION = "2025-06-17"


class PlacesApiError(RuntimeError):
    pass


class PlacesAuthError(PlacesApiError):
    pass


class PlacesQuotaError(PlacesApiError):
    pass


class PlacesAPI:
    def __init__(
        self,
        service_key: str,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = 30.0,
        sleep: float = 0.0,
        retries: int = 3,
        verbose: bool = False,
    ) -> None:
        self.service_key = service_key
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout = timeout
        self.sleep = sleep
        self.retries = retries
        self.verbose = verbose

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        query = {key: value for key, value in (params or {}).items() if value is not None}
        url = f"{self.base_url}/{path.lstrip('/')}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            started = time.time()
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.service_key}",
                        "Accept": "application/json",
                        "X-Places-Api-Version": self.api_version,
                        "User-Agent": "spot-history-manager/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                self._log(f"GET {path} {time.time() - started:.2f}s")
                time.sleep(self.sleep)
                return payload
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {}
                detail = (payload or {}).get("message") or body[:200]
                if exc.code in (401, 403):
                    raise PlacesAuthError(
                        f"Places API verweigert den Zugriff ({exc.code}): {detail}. "
                        "FOURSQUARE_SERVICE_API_KEY prüfen."
                    ) from exc
                if exc.code == 429:
                    raise PlacesQuotaError(
                        f"Places API ohne Credits ({exc.code}): {detail}"
                    ) from exc
                last_error = PlacesApiError(f"{exc.code}: {detail}")
                if 500 <= exc.code < 600:
                    wait = min(2 ** attempt, 30)
                    self._log(f"HTTP {exc.code}, retry in {wait}s")
                    time.sleep(wait)
                    continue
                raise last_error from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                last_error = exc
                wait = min(2 ** attempt, 30)
                self._log(f"Netzwerkfehler ({exc}), retry in {wait}s")
                time.sleep(wait)

        raise PlacesApiError(
            f"Anfrage nach {self.retries} Versuchen fehlgeschlagen: {last_error}"
        )

    def venue_details(self, fsq_place_id: str) -> dict[str, Any]:
        return self.get(f"places/{urllib.parse.quote(fsq_place_id)}") or {}
