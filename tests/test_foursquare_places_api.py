from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from contextlib import redirect_stdout
from unittest import mock

from spot_history.connectors.foursquare.config import build_config
from spot_history.connectors.foursquare.places_api import (
    PlacesAPI,
    PlacesAuthError,
    PlacesQuotaError,
)


def http_error(code: int, message: str) -> urllib.error.HTTPError:
    body = json.dumps({"message": message}).encode("utf-8")
    return urllib.error.HTTPError(
        "https://places-api.foursquare.com/places/x",
        code,
        message,
        {},
        io.BytesIO(body),
    )


class FakeResponse:
    def __init__(self, payload) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class FakeCursor:
    def fetchone(self):
        return None


class FakeConnection:
    def execute(self, *args, **kwargs):
        return FakeCursor()


class FakeDatabase:
    conn = FakeConnection()


class FakeApi:
    def _request(self, path, params):
        return {}


class PlacesApiTest(unittest.TestCase):
    def test_venue_details_ok(self):
        payload = {"fsq_place_id": "abc", "name": "Cafe", "tel": "123"}
        with mock.patch(
            "urllib.request.urlopen", return_value=FakeResponse(payload)
        ) as urlopen:
            api = PlacesAPI("key", retries=1)
            self.assertEqual(api.venue_details("abc"), payload)
        request = urlopen.call_args[0][0]
        self.assertIn("/places/abc", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer key")
        self.assertEqual(
            request.get_header("X-places-api-version"), "2025-06-17"
        )

    def test_auth_error_has_no_retry(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=http_error(401, "Invalid token")
        ) as urlopen:
            api = PlacesAPI("key", retries=3)
            with self.assertRaises(PlacesAuthError):
                api.venue_details("abc")
        self.assertEqual(urlopen.call_count, 1)

    def test_quota_error_has_no_retry(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=http_error(429, "No credits")
        ) as urlopen:
            api = PlacesAPI("key", retries=3)
            with self.assertRaises(PlacesQuotaError):
                api.venue_details("abc")
        self.assertEqual(urlopen.call_count, 1)

    def test_server_error_is_retried(self):
        with mock.patch(
            "urllib.request.urlopen", side_effect=http_error(500, "Server error")
        ) as urlopen, mock.patch("time.sleep"):
            api = PlacesAPI("key", retries=2)
            with self.assertRaises(Exception):
                api.venue_details("abc")
        self.assertEqual(urlopen.call_count, 2)

    def test_config_reads_service_key(self):
        env = {"FOURSQUARE_SERVICE_API_KEY": "svc"}
        with mock.patch.dict(os.environ, env, clear=False):
            config = build_config(require_token=False)
        self.assertEqual(config.service_api_key, "svc")
        self.assertEqual(
            config.places_base_url, "https://places-api.foursquare.com"
        )

    def test_probe_skips_without_service_key(self):
        from spot_history.connectors.foursquare.connector import CONNECTOR

        out = io.StringIO()
        with redirect_stdout(out):
            CONNECTOR.probe(FakeApi(), FakeDatabase(), places=None)
        self.assertIn("FOURSQUARE_SERVICE_API_KEY fehlt", out.getvalue())
        self.assertNotIn("Places API (nur lesend)", out.getvalue())


if __name__ == "__main__":
    unittest.main()
