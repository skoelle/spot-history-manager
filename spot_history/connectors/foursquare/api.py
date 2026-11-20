from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ApiError(RuntimeError):
    pass


class AuthError(ApiError):
    pass


class FoursquareAPI:
    def __init__(
        self,
        token: str,
        version: str = "20240101",
        base_url: str = "https://api.foursquare.com/v2",
        timeout: float = 60.0,
        sleep: float = 0.3,
        retries: int = 4,
        verbose: bool = False,
    ) -> None:
        self.token = token
        self.version = version
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.sleep = sleep
        self.retries = retries
        self.verbose = verbose

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def _request(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = {key: value for key, value in params.items() if value is not None}
        query["oauth_token"] = self.token
        query["v"] = self.version
        url = f"{self.base_url}/{path.lstrip('/')}?{urllib.parse.urlencode(query)}"
        last_error: Exception | None = None

        for attempt in range(1, self.retries + 1):
            started = time.time()
            try:
                request = urllib.request.Request(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "spot-history-manager/1.0",
                    },
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    payload = json.load(response)
                elapsed = time.time() - started
                self._log(f"GET {path} {query.get('limit', '')} {elapsed:.2f}s")
                meta = payload.get("meta") or {}
                code = meta.get("code")
                if code not in (None, 200):
                    detail = meta.get("errorDetail") or meta.get("errorType")
                    if code in (401, 403):
                        raise AuthError(f"{code}: {detail}")
                    raise ApiError(f"{code}: {detail}")
                time.sleep(self.sleep)
                return payload.get("response") or {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = {}
                meta = payload.get("meta") or {}
                detail = meta.get("errorDetail") or meta.get("errorType") or body[:200]
                if exc.code in (401, 403) or meta.get("errorType") in (
                    "invalid_auth",
                    "not_authorized",
                ):
                    raise AuthError(
                        f"Foursquare verweigert den Zugriff ({exc.code}): {detail}. "
                        "Token erneuern (OAuth-Flow) und FOURSQUARE_OAUTH_TOKEN aktualisieren."
                    ) from exc
                last_error = ApiError(f"{exc.code}: {detail}")
                if exc.code == 429 or 500 <= exc.code < 600:
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

        raise ApiError(f"Anfrage nach {self.retries} Versuchen fehlgeschlagen: {last_error}")

    def self_user(self) -> dict[str, Any]:
        return self._request("users/self", {}).get("user") or {}

    def checkins(
        self,
        before: int | None = None,
        after: int | None = None,
        limit: int = 250,
        sort: str | None = None,
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": str(limit)}
        if before is not None:
            params["beforeTimestamp"] = str(before)
        if after is not None:
            params["afterTimestamp"] = str(after)
        if sort:
            params["sort"] = sort
        response = self._request("users/self/checkins", params)
        block = response.get("checkins") or {}
        return block.get("items") or [], block.get("count")

    def comments(self, checkin_id: str) -> list[dict[str, Any]]:
        response = self._request(f"checkins/{checkin_id}/comments", {})
        block = response.get("comments") or {}
        return block.get("items") or []

    def photos(
        self, limit: int = 500, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": str(limit), "offset": str(offset)}
        response = self._request("users/self/photos", params)
        block = response.get("photos") or {}
        return block.get("items") or [], block.get("count")

    def venue_history(
        self, limit: int = 500, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": str(limit), "offset": str(offset)}
        response = self._request("users/self/venuehistory", params)
        block = response.get("venues") or {}
        return block.get("items") or [], block.get("count")

    def lists(self, limit: int = 50) -> list[dict[str, Any]]:
        response = self._request("users/self/lists", {"limit": str(limit)})
        block = response.get("lists") or {}
        items: list[dict[str, Any]] = []
        for group in block.get("groups") or []:
            items.extend(group.get("items") or [])
        return items

    def list_details(
        self, list_id: str, limit: int = 100, offset: int = 0
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": str(limit), "offset": str(offset)}
        response = self._request(f"lists/{list_id}", params)
        return response.get("list") or {}

    def tips(
        self, limit: int = 50, offset: int = 0
    ) -> tuple[list[dict[str, Any]], int | None]:
        params: dict[str, Any] = {"limit": str(limit), "offset": str(offset)}
        response = self._request("users/self/tips", params)
        block = response.get("tips") or {}
        return block.get("items") or [], block.get("count")

    def tip_details(self, tip_id: str) -> dict[str, Any]:
        response = self._request(f"tips/{tip_id}", {})
        return response.get("tip") or {}

    def tastes(self) -> list[dict[str, Any]]:
        response = self._request("users/self/tastes", {})
        block = response.get("tastes") or {}
        items = block.get("items")
        if items is not None:
            return items
        collected: list[dict[str, Any]] = []
        for group in block.get("groups") or []:
            collected.extend(group.get("items") or [])
        return collected
