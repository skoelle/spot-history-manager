# Copyright (c) 2026 Stefan Koelle (https://stefankoelle.de)
# Licensed under the MIT License. See LICENSE file in the project root for details.
from __future__ import annotations

import asyncio
import csv
import importlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import urlencode


def _body(text: str) -> bytes:
    return urlencode(
        {"decisions": text, "submit_action": "apply"}
    ).encode("utf-8")


def _decisions() -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "ref",
            "action",
            "target",
            "osm",
            "state",
            "lat",
            "lng",
            "address",
            "city",
            "postal_code",
            "confidence",
            "note",
        ]
    )
    writer.writerow(
        ["yelp:test", "state", "", "", "no_osm", "", "", "", "", "", "", "test"]
    )
    return buffer.getvalue()


class ResearchRouteTest(unittest.TestCase):
    def test_apply_starts_predefined_action(self):
        old_env = dict(os.environ)
        try:
            server = importlib.import_module("spot_history.web.server")
            request = SimpleNamespace(
                headers={},
                url=SimpleNamespace(hostname="test"),
                body=mock.AsyncMock(return_value=_body(_decisions())),
            )
            with tempfile.TemporaryDirectory() as tmp:
                target = Path(tmp) / "decisions.csv"
                with (
                    mock.patch.object(
                        server.sync_jobs,
                        "research_decisions_path",
                        return_value=target,
                    ),
                    mock.patch.object(
                        server.sync_runner, "start", return_value=None
                    ) as start,
                ):
                    response = asyncio.run(server.sync_research(request))
                self.assertEqual(response.status_code, 303)
                self.assertTrue(target.exists())
                self.assertIn("yelp:test", target.read_text(encoding="utf-8"))
                start.assert_called_once_with(
                    server.paths.data_root, "yelp-apply-research"
                )
        finally:
            os.environ.clear()
            os.environ.update(old_env)


if __name__ == "__main__":
    unittest.main()
