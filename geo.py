"""
Station coordinates + route map helpers.

Coords come from the datameet/railways community GeoJSON
(https://github.com/datameet/railways) converted to station_coords.csv.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Optional

import pandas as pd

_COORDS_FILE = "station_coords.csv"


@lru_cache(maxsize=1)
def _load_coords() -> dict:
    try:
        df = pd.read_csv(_COORDS_FILE, dtype={"station_code": str})
    except FileNotFoundError:
        return {}
    df["station_code"] = df["station_code"].fillna("").str.strip().str.upper()
    df["lat"] = pd.to_numeric(df["lat"], errors="coerce")
    df["lon"] = pd.to_numeric(df["lon"], errors="coerce")
    df = df.dropna(subset=["station_code", "lat", "lon"])
    out = {}
    for row in df.itertuples(index=False):
        out[row.station_code] = {
            "lat": float(row.lat),
            "lon": float(row.lon),
            "name": str(getattr(row, "station_name", "") or ""),
        }
    return out


def get_station_coords(station_code: str) -> Optional[dict]:
    code = (station_code or "").strip().upper()
    if not code:
        return None
    return _load_coords().get(code)


def coords_coverage_stats() -> dict:
    coords = _load_coords()
    return {"stations_with_coords": len(coords), "file": _COORDS_FILE}


def connection_map_points(
    start_code: str,
    via_code: str,
    end_code: str,
    start_label: str = "",
    via_label: str = "",
    end_label: str = "",
) -> pd.DataFrame:
    """
    Points for a one-change route: Start → Via → End.
    Returns empty frame if any required coord is missing.
    """
    rows = []
    for code, label, role in (
        (start_code, start_label or start_code, "Start"),
        (via_code, via_label or via_code, "Via"),
        (end_code, end_label or end_code, "End"),
    ):
        c = get_station_coords(code)
        if not c:
            continue
        rows.append(
            {
                "lat": c["lat"],
                "lon": c["lon"],
                "code": (code or "").upper(),
                "name": label or c.get("name") or code,
                "role": role,
                "size": 180 if role == "Via" else 120,
            }
        )
    return pd.DataFrame(rows)


def two_change_map_points(
    start_code: str,
    via1_code: str,
    via2_code: str,
    end_code: str,
) -> pd.DataFrame:
    rows = []
    for code, role in (
        (start_code, "Start"),
        (via1_code, "Via 1"),
        (via2_code, "Via 2"),
        (end_code, "End"),
    ):
        c = get_station_coords(code)
        if not c:
            continue
        rows.append(
            {
                "lat": c["lat"],
                "lon": c["lon"],
                "code": (code or "").upper(),
                "name": c.get("name") or code,
                "role": role,
                "size": 160,
            }
        )
    return pd.DataFrame(rows)


def schedule_map_points(train_number: str, schedule_df: pd.DataFrame) -> pd.DataFrame:
    """Map all stops of one train that have coordinates."""
    if schedule_df is None or schedule_df.empty:
        return pd.DataFrame()
    code_col = "Code" if "Code" in schedule_df.columns else "station_code"
    name_col = "Station" if "Station" in schedule_df.columns else "station_name"
    rows = []
    for i, row in schedule_df.iterrows():
        code = str(row.get(code_col) or "").strip().upper()
        c = get_station_coords(code)
        if not c:
            continue
        rows.append(
            {
                "lat": c["lat"],
                "lon": c["lon"],
                "code": code,
                "name": str(row.get(name_col) or c.get("name") or code),
                "role": f"Stop {len(rows)+1}",
                "size": 80,
                "train": str(train_number),
            }
        )
    return pd.DataFrame(rows)
