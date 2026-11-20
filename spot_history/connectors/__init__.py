from __future__ import annotations

import importlib
import pkgutil
from pathlib import Path

from .base import BaseConnector


def discover() -> dict[str, BaseConnector]:
    found: dict[str, BaseConnector] = {}
    package = __name__
    base = Path(__file__).parent
    for info in pkgutil.iter_modules([str(base)]):
        if not info.ispkg or info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        connector = getattr(module, "CONNECTOR", None)
        if isinstance(connector, BaseConnector):
            found[connector.name] = connector
    return found
