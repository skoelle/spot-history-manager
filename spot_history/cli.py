from __future__ import annotations

import argparse
import os
import sys

from .connectors import discover
from .core.config import build_paths
from .core.index import build_index


def _run_index(args: argparse.Namespace, connectors) -> int:
    paths = build_paths(args.data_root)
    summary = build_index(connectors, paths)
    print(f"Quellen:  {', '.join(summary['sources']) or '-'}")
    print(f"Visits:   {summary['visits']}")
    print(f"Fotos:    {summary['photos']}")
    print(f"Punkte:   {summary['features']}")
    print(f"Index:    {summary['index_db']}")
    print(f"GeoJSON:  {summary['geojson']}")
    return 0


def _run_web(args: argparse.Namespace) -> int:
    if args.data_root:
        os.environ["SPOT_HISTORY_DATA_ROOT"] = args.data_root
    try:
        import uvicorn
    except ImportError:
        print(
            "FastAPI/uvicorn fehlen. Bitte im venv installieren: "
            ".venv/bin/pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1
    uvicorn.run(
        "spot_history.web.server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    connectors = discover()
    parser = argparse.ArgumentParser(
        prog="spot_history",
        description="Check-in-Historie mehrerer Dienste verwalten und zentral indexieren",
    )
    subparsers = parser.add_subparsers(dest="component", required=True)

    for connector in connectors.values():
        connector.add_parser(subparsers)

    index_parser = subparsers.add_parser("index", help="zentralen Index bauen")
    index_commands = index_parser.add_subparsers(dest="command", required=True)
    build_parser = index_commands.add_parser("build", help="Index + GeoJSON neu bauen")
    build_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    build_parser.set_defaults(func=lambda args: _run_index(args, connectors))

    web_parser = subparsers.add_parser("web", help="Web-Oberfläche starten")
    web_parser.add_argument("--host", default="0.0.0.0")
    web_parser.add_argument("--port", type=int, default=8345)
    web_parser.add_argument("--data-root", help="Zentrales Datenverzeichnis (default: ./data)")
    web_parser.add_argument("--reload", action="store_true")
    web_parser.set_defaults(func=_run_web)

    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
