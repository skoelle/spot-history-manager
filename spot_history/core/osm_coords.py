from __future__ import annotations

import sqlite3
from pathlib import Path


def load_osm_coords(path: Path) -> dict[tuple[str, int], tuple[float, float]]:
    """Read cached OSM POI coordinates; missing DB/table yields an empty map."""
    if not path.exists():
        return {}
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        try:
            rows = conn.execute(
                "SELECT osm_type, osm_id, lat, lng FROM pois "
                "WHERE lat IS NOT NULL AND lng IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
    finally:
        conn.close()
    coords: dict[tuple[str, int], tuple[float, float]] = {}
    for row in rows:
        try:
            coords[(row["osm_type"], int(row["osm_id"]))] = (
                float(row["lat"]),
                float(row["lng"]),
            )
        except (TypeError, ValueError):
            continue
    return coords
