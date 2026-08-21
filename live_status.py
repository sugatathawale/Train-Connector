"""
Live train running status / delay via NTES (National Train Enquiry System).

Uses the unofficial `ntes` Python client against enquiry.indianrail.gov.in.
There is no official public developer API — this can break if NTES changes.

Delay is used to flag tight layovers as risky (e.g. 45-min wait with 30-min late).
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

_NTES = None
_NTES_ERR = ""


def _get_client():
    global _NTES, _NTES_ERR
    if _NTES is not None:
        return _NTES
    if _NTES_ERR:
        return None
    try:
        from ntes import NTESClient

        _NTES = NTESClient(timeout=25, retries=1)
        return _NTES
    except Exception as exc:  # noqa: BLE001
        _NTES_ERR = f"ntes-client not available: {exc}"
        return None


def _fmt_ntes_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def _parse_delay_text(text: str) -> Optional[int]:
    """Parse strings like 'Late by 45 mins', '45 Min Late', 'On Time'."""
    s = (text or "").strip()
    if not s:
        return None
    up = s.upper()
    if "ON TIME" in up or up in ("RT", "RIGHT TIME"):
        return 0
    m = re.search(r"(\d+)\s*(?:MIN|MINUTE)", up)
    if m:
        mins = int(m.group(1))
        if "EARLY" in up:
            return -mins
        return mins
    m = re.search(r"LATE\s*(?:BY\s*)?(\d+)", up)
    if m:
        return int(m.group(1))
    return None


def _station_delay_minutes(stn: dict) -> int:
    """Best delay estimate for one NTES station row."""
    df = stn.get("DF")
    try:
        if df is not None and str(df).strip() != "":
            return int(float(df))
    except (TypeError, ValueError):
        pass
    for key in ("DARR", "DDEP"):
        parsed = _parse_delay_text(str(stn.get(key) or ""))
        if parsed is not None:
            return parsed
    return 0


def _overall_delay_from_payload(payload: dict) -> int:
    """Current delay: prefer last reported station with activity, else max DF."""
    stations = payload.get("STNS") or []
    if not isinstance(stations, list):
        return 0
    # Prefer station marked as current / last updated sequence
    best = 0
    for stn in stations:
        if not isinstance(stn, dict):
            continue
        d = _station_delay_minutes(stn)
        if abs(d) >= abs(best):
            best = d
        # ISA/ISD sometimes mark arrived/departed
        if stn.get("ISA") or stn.get("ISD"):
            best = d
    return best


def delay_at_station(payload: dict, station_code: str) -> Optional[int]:
    """Delay minutes at a specific station code, if present in the live list."""
    code = (station_code or "").strip().upper()
    if not code or not isinstance(payload, dict):
        return None
    # Raw NTES shape
    for stn in payload.get("STNS") or []:
        if not isinstance(stn, dict):
            continue
        if str(stn.get("SC") or "").strip().upper() == code:
            return _station_delay_minutes(stn)
    # Normalized shape from fetch_train_live_status
    for stn in payload.get("stations") or []:
        if not isinstance(stn, dict):
            continue
        if str(stn.get("code") or "").strip().upper() == code:
            try:
                return int(stn.get("delay_mins") or 0)
            except (TypeError, ValueError):
                return 0
    return None


def fetch_train_live_status(
    train_number: str,
    start_date: date,
) -> dict:
    """
    Live running status for a train instance that left origin on start_date.

    Returns {ok, error, train_number, start_date, delay_mins, position, last_update,
             stations: [{code, name, delay_mins, darr, ddep, eta, etd}], raw_cpos}.
    """
    train_number = str(train_number or "").strip()
    if not train_number:
        return {"ok": False, "error": "Missing train number", "delay_mins": 0, "stations": []}
    if start_date is None:
        return {"ok": False, "error": "Missing journey start date", "delay_mins": 0, "stations": []}

    client = _get_client()
    if client is None:
        return {
            "ok": False,
            "error": _NTES_ERR or "Install ntes-client to enable live status (pip install ntes-client)",
            "delay_mins": 0,
            "stations": [],
            "train_number": train_number,
        }

    try:
        payload = client.live_status(train_number, _fmt_ntes_date(start_date))
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"NTES live status failed: {exc}",
            "delay_mins": 0,
            "stations": [],
            "train_number": train_number,
            "start_date": start_date.isoformat(),
        }

    if not isinstance(payload, dict) or not payload:
        return {
            "ok": False,
            "error": "Empty NTES response",
            "delay_mins": 0,
            "stations": [],
            "train_number": train_number,
            "start_date": start_date.isoformat(),
        }

    stations_out = []
    for stn in payload.get("STNS") or []:
        if not isinstance(stn, dict):
            continue
        code = str(stn.get("SC") or "").strip().upper()
        if not code:
            continue
        stations_out.append(
            {
                "code": code,
                "name": str(stn.get("SN") or "").strip(),
                "delay_mins": _station_delay_minutes(stn),
                "darr": str(stn.get("DARR") or ""),
                "ddep": str(stn.get("DDEP") or ""),
                "eta": str(stn.get("ETA") or ""),
                "etd": str(stn.get("ETD") or ""),
                "pf": str(stn.get("PF") or ""),
            }
        )

    delay = _overall_delay_from_payload(payload)
    return {
        "ok": True,
        "error": "",
        "train_number": train_number,
        "train_name": str(payload.get("TNM") or ""),
        "start_date": start_date.isoformat(),
        "delay_mins": int(delay),
        "position": str(payload.get("CPOS") or ""),
        "last_update": str(payload.get("LUPDT") or payload.get("LASTUPD") or ""),
        "stations": stations_out,
        "source": "NTES",
    }


def origin_start_date_for_boarding(
    board_date: date,
    schedule_day_at_board: int,
) -> date:
    """
    NTES live_status wants the train's ORIGIN departure date.
    schedule_day_at_board is 1 at origin, 2 next day, …
    """
    offset = max(int(schedule_day_at_board or 1) - 1, 0)
    return board_date - timedelta(days=offset)


def assess_layover_risk(
    layover_hrs: float,
    arriving_delay_mins: int,
    buffer_mins: int = 20,
) -> dict:
    """
    Compare planned layover vs arriving-train delay.

    buffer_mins = minimum comfortable transfer time after accounting for delay.
    """
    try:
        layover_mins = float(layover_hrs) * 60.0
    except (TypeError, ValueError):
        layover_mins = 0.0
    delay = max(0, int(arriving_delay_mins or 0))
    remaining = layover_mins - delay
    if remaining < 0:
        level = "missed"
        label = (
            f"High risk — train ~{delay} min late; "
            f"{layover_mins:.0f} min wait may not be enough"
        )
    elif remaining < buffer_mins:
        level = "risky"
        label = (
            f"Risky change — ~{delay} min late leaves only "
            f"{remaining:.0f} min (want ≥{buffer_mins} min)"
        )
    elif delay >= 15:
        level = "watch"
        label = f"Watch — arriving train ~{delay} min late; {remaining:.0f} min still left"
    else:
        level = "ok"
        label = (
            f"OK — delay ~{delay} min; ~{remaining:.0f} min transfer left"
            if delay
            else f"OK — on time; {layover_mins:.0f} min planned wait"
        )
    return {
        "level": level,
        "label": label,
        "layover_mins": round(layover_mins, 1),
        "delay_mins": delay,
        "remaining_mins": round(remaining, 1),
        "buffer_mins": buffer_mins,
    }


def check_connection_delay_risk(
    train1_no: str,
    via_code: str,
    layover_hrs: float,
    board_date: date,
    train1_start_day: int = 1,
    buffer_mins: int = 20,
) -> dict:
    """
    Fetch live status for Train 1 and score the via layover.
    """
    start = origin_start_date_for_boarding(board_date, train1_start_day)
    status = fetch_train_live_status(train1_no, start)
    if not status.get("ok"):
        return {
            "ok": False,
            "error": status.get("error") or "Live status unavailable",
            "status": status,
            "risk": None,
        }

    via_delay = delay_at_station(status, via_code)
    delay = status.get("delay_mins") or 0
    if via_delay is not None:
        delay = via_delay

    risk = assess_layover_risk(layover_hrs, delay, buffer_mins=buffer_mins)
    return {
        "ok": True,
        "error": "",
        "status": status,
        "via_code": via_code,
        "delay_mins": delay,
        "risk": risk,
    }


def check_connection_pair_status(
    train1_no: str,
    train2_no: str,
    via_code: str,
    layover_hrs: float,
    board_date: date,
    train1_start_day: int = 1,
    train2_day_offset: int = 0,
    train2_mid_day: int = 1,
    buffer_mins: int = 20,
) -> dict:
    """Fetch Train 1 + Train 2 live status in parallel; risk from Train 1 at via."""
    t1_start = origin_start_date_for_boarding(board_date, train1_start_day)
    t2_board = board_date + timedelta(days=int(train2_day_offset or 0))
    t2_start = origin_start_date_for_boarding(t2_board, train2_mid_day)

    results: dict[str, Any] = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        futs = {
            pool.submit(fetch_train_live_status, train1_no, t1_start): "leg1",
            pool.submit(fetch_train_live_status, train2_no, t2_start): "leg2",
        }
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                results[key] = fut.result()
            except Exception as exc:  # noqa: BLE001
                results[key] = {"ok": False, "error": str(exc), "delay_mins": 0, "stations": []}

    leg1 = results.get("leg1") or {}
    leg2 = results.get("leg2") or {}
    delay = int(leg1.get("delay_mins") or 0)
    via_delay = delay_at_station(leg1, via_code)
    if via_delay is not None:
        delay = via_delay

    risk = None
    if leg1.get("ok"):
        risk = assess_layover_risk(layover_hrs, delay, buffer_mins=buffer_mins)

    return {
        "ok": bool(leg1.get("ok") or leg2.get("ok")),
        "leg1": leg1,
        "leg2": leg2,
        "delay_mins": delay,
        "risk": risk,
        "via_code": via_code,
    }
