"""
Live seat availability via ConfirmTkt public enquiry API.

Fetches both connection legs in parallel so users see Train 1 + Train 2
availability together (not one train at a time).
"""

from __future__ import annotations

import re
import secrets
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any, Optional

import requests

_BASE = "https://securedapi.confirmtkt.com/"
_ROUTE = "api/platform/trainbooking/tatwnstns"
_TIMEOUT = 45

_HEADERS = {
    "Host": "securedapi.confirmtkt.com",
    "Connection": "Keep-Alive",
    "User-Agent": "okhttp/4.9.2",
    "Accept": "application/json",
}


def _session_token(n: int = 32) -> str:
    return secrets.token_hex(n // 2)


def _format_doj(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def _normalize_train_no(value: Any) -> str:
    s = str(value or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.lstrip("0") or "0"


def _class_status(avl: dict) -> dict:
    """Flatten one class availability row into a small dict."""
    status = (
        avl.get("Availability")
        or avl.get("availability")
        or avl.get("availablityStatus")
        or avl.get("AvailablityStatus")
        or avl.get("Status")
        or ""
    )
    prediction = avl.get("ConfirmTktPrediction") or avl.get("Prediction") or avl.get("prediction") or ""
    chance = (
        avl.get("ConfirmTktPercent")
        or avl.get("PredictionPercentage")
        or avl.get("chance")
        or ""
    )
    fare = avl.get("Fare") or avl.get("fare") or avl.get("totalFare") or ""
    travel_class = (
        avl.get("Class")
        or avl.get("className")
        or avl.get("TravelClass")
        or avl.get("class")
        or ""
    )
    return {
        "class": str(travel_class).strip(),
        "status": str(status).strip(),
        "prediction": str(prediction).strip(),
        "chance": str(chance).strip(),
        "fare": str(fare).strip() if fare not in (None, "") else "",
    }


def _extract_trains(payload: Any) -> list[dict]:
    if payload is None:
        return []
    if isinstance(payload, list):
        return [t for t in payload if isinstance(t, dict)]
    if not isinstance(payload, dict):
        return []

    for key in (
        "trainBtwnStnsList",
        "trains",
        "TrainBtwnStnsList",
        "trainList",
        "data",
        "result",
    ):
        val = payload.get(key)
        if isinstance(val, list):
            return [t for t in val if isinstance(t, dict)]
        if isinstance(val, dict):
            nested = _extract_trains(val)
            if nested:
                return nested
    return []


def _class_codes_only(avl_classes) -> list[str]:
    if isinstance(avl_classes, list):
        return [str(c) for c in avl_classes]
    if isinstance(avl_classes, dict):
        arr = avl_classes.get("Array") or avl_classes.get("array") or []
        if isinstance(arr, list):
            return [str(c) for c in arr]
    return []


def _extract_classes(train: dict) -> list[dict]:
    # ConfirmTkt primary shape: avaiblitycache / avaiblitycacheTq = { "SL": {...}, ... }
    for key in ("avaiblitycache", "availabilityCache", "AvailabilityCache", "avaiblitycacheTq"):
        cache = train.get(key)
        if isinstance(cache, dict) and cache:
            out = []
            for cls, row in cache.items():
                if not isinstance(row, dict):
                    continue
                parsed = _class_status(row)
                if not parsed["class"]:
                    parsed["class"] = str(cls)
                # Prefer human display names when present
                if row.get("AvailabilityDisplayName"):
                    parsed["status"] = str(row["AvailabilityDisplayName"]).strip()
                if row.get("PredictionDisplayName"):
                    parsed["prediction"] = str(row["PredictionDisplayName"]).strip()
                out.append(parsed)
            if out:
                return out

    for key in (
        "avlClasses",
        "availability",
        "Availabilities",
        "Availablity",
        "classAvailList",
        "tbsAvailability",
        "AvailabilityCacheList",
    ):
        raw = train.get(key)
        if isinstance(raw, list) and raw:
            out = []
            for row in raw:
                if isinstance(row, dict):
                    out.append(_class_status(row))
                elif isinstance(row, str):
                    out.append({"class": row, "status": "—", "prediction": "", "chance": "", "fare": ""})
            return out
        if isinstance(raw, dict):
            codes = _class_codes_only(raw)
            if codes:
                return [
                    {"class": c, "status": "—", "prediction": "", "chance": "", "fare": ""}
                    for c in codes
                ]

    avl_map = train.get("avlDayList") or train.get("classAvailability")
    if isinstance(avl_map, dict) and avl_map:
        return [
            {
                "class": str(k),
                "status": str(v.get("status", v) if isinstance(v, dict) else v),
                "prediction": "",
                "chance": "",
                "fare": "",
            }
            for k, v in avl_map.items()
        ]
    return []


def _parse_train(train: dict) -> dict:
    number = (
        train.get("trainNumber")
        or train.get("trainNo")
        or train.get("TrainNumber")
        or train.get("TrainNo")
        or ""
    )
    name = (
        train.get("trainName")
        or train.get("TrainName")
        or train.get("train_name")
        or ""
    )
    classes = _extract_classes(train)

    return {
        "train_number": _normalize_train_no(number),
        "train_name": str(name).strip(),
        "classes": classes,
        "raw_has_data": bool(classes) or bool(number),
    }


def fetch_route_availability(
    from_code: str,
    to_code: str,
    journey_date: date,
    quota: str = "GN",
) -> dict:
    """
    Live availability for all trains on from→to for a date.
    Returns {ok, error, from, to, date, trains: [...]}.
    """
    from_code = (from_code or "").strip().upper()
    to_code = (to_code or "").strip().upper()
    if not from_code or not to_code:
        return {"ok": False, "error": "Missing station codes", "trains": []}
    if from_code == to_code:
        return {"ok": False, "error": "From and to stations are the same", "trains": []}

    params = {
        "fromStnCode": from_code,
        "destStnCode": to_code,
        "doj": _format_doj(journey_date),
        "quota": quota or "GN",
        "token": _session_token(64),
        "androidid": "",
        "travelClassOrdering": "ON,Ixigo",
        "appVersion": "397",
        "prevBookedTrains": "OFF",
        "noChancePercentage": "true",
        "getNearbyStation": "true",
        "session": _session_token(32),
    }

    try:
        resp = requests.get(
            _BASE + _ROUTE,
            params=params,
            headers=_HEADERS,
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return {
                "ok": False,
                "error": f"Availability API returned HTTP {resp.status_code}",
                "trains": [],
                "from": from_code,
                "to": to_code,
                "date": _format_doj(journey_date),
            }
        payload = resp.json()
        trains = [_parse_train(t) for t in _extract_trains(payload)]
        return {
            "ok": True,
            "error": "",
            "from": from_code,
            "to": to_code,
            "date": _format_doj(journey_date),
            "trains": trains,
            "quota": quota,
        }
    except requests.Timeout:
        return {
            "ok": False,
            "error": "Timed out waiting for live availability",
            "trains": [],
            "from": from_code,
            "to": to_code,
            "date": _format_doj(journey_date),
        }
    except requests.RequestException as exc:
        return {
            "ok": False,
            "error": f"Network error: {exc}",
            "trains": [],
            "from": from_code,
            "to": to_code,
            "date": _format_doj(journey_date),
        }
    except ValueError:
        return {
            "ok": False,
            "error": "Could not parse availability response",
            "trains": [],
            "from": from_code,
            "to": to_code,
            "date": _format_doj(journey_date),
        }


def pick_train(route_result: dict, train_number: str) -> dict:
    """Pick one train from a route result; keep route meta either way."""
    want = _normalize_train_no(train_number)
    trains = route_result.get("trains") or []
    match = next((t for t in trains if t.get("train_number") == want), None)
    return {
        "ok": bool(route_result.get("ok")),
        "error": route_result.get("error") or "",
        "from": route_result.get("from"),
        "to": route_result.get("to"),
        "date": route_result.get("date"),
        "train_number": want,
        "train_name": (match or {}).get("train_name", ""),
        "classes": (match or {}).get("classes") or [],
        "found": match is not None,
    }


def fetch_connection_availability(
    start_code: str,
    via_code: str,
    end_code: str,
    train1_no: str,
    train2_no: str,
    journey_date: date,
    train2_day_offset: int = 0,
    quota: str = "GN",
) -> dict:
    """
    Scrape BOTH legs together (parallel) and return seat status for each train.
    """
    date1 = journey_date
    date2 = journey_date + timedelta(days=int(train2_day_offset or 0))

    leg1_raw: dict = {}
    leg2_raw: dict = {}

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(fetch_route_availability, start_code, via_code, date1, quota): "leg1",
            pool.submit(fetch_route_availability, via_code, end_code, date2, quota): "leg2",
        }
        for fut in as_completed(futures):
            key = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:  # noqa: BLE001 — surface any worker failure
                result = {"ok": False, "error": str(exc), "trains": []}
            if key == "leg1":
                leg1_raw = result
            else:
                leg2_raw = result

    leg1 = pick_train(leg1_raw, train1_no)
    leg2 = pick_train(leg2_raw, train2_no)

    return {
        "ok": bool(leg1.get("ok") or leg2.get("ok")),
        "quota": quota,
        "journey_date": _format_doj(journey_date),
        "leg1": leg1,
        "leg2": leg2,
    }


def status_tone(status: str) -> str:
    """CSS-friendly tone label for a status string."""
    s = (status or "").upper()
    if "AVAILABLE" in s or s.startswith("AVL") or "CNF" in s:
        return "good"
    if "RAC" in s:
        return "warn"
    if "WL" in s or "WAIT" in s:
        return "wait"
    if "REGRET" in s or "NOT AVAILABLE" in s or "NQTW" in s:
        return "bad"
    return "muted"


def format_class_line(row: dict) -> str:
    parts = [row.get("class") or "?", row.get("status") or "—"]
    if row.get("prediction"):
        chance = row.get("chance")
        if chance:
            parts.append(f"{row['prediction']} ({chance}%)")
        else:
            parts.append(row["prediction"])
    if row.get("fare"):
        parts.append(f"₹{row['fare']}")
    return " · ".join(parts)


# Preferred travel classes shown in the UI filter.
TRAVEL_CLASSES = ["SL", "3A", "2A", "1A", "CC", "EC", "2S", "3E", "EA"]


def _norm_class(value: str) -> str:
    return str(value or "").strip().upper().replace(" ", "")


def filter_classes(classes: list[dict], preferred: Optional[list[str]] = None) -> list[dict]:
    """Keep only preferred classes when a preference list is set."""
    rows = classes or []
    if not preferred:
        return rows
    want = {_norm_class(c) for c in preferred if c}
    if not want:
        return rows
    matched = [r for r in rows if _norm_class(r.get("class")) in want]
    return matched if matched else rows


def _parse_fare(value) -> Optional[float]:
    if value is None or value == "":
        return None
    s = re.sub(r"[^\d.]", "", str(value))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _parse_chance(value) -> Optional[float]:
    if value is None or value == "":
        return None
    s = re.sub(r"[^\d.]", "", str(value))
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _wl_number(status: str) -> int:
    s = (status or "").upper()
    m = re.search(r"(?:WL|WAIT(?:LIST)?)[^\d]*(\d+)", s)
    if m:
        try:
            return int(m.group(1))
        except ValueError:
            return 99
    if "WL" in s or "WAIT" in s:
        return 50
    return 99


def score_class_row(row: dict) -> float:
    """
    Higher = more bookable.
    AVAILABLE/AVL/CNF ≈ 100, RAC ≈ 60, short WL better than long WL, REGRET = 0.
    """
    status = str(row.get("status") or "").upper()
    tone = status_tone(status)
    chance = _parse_chance(row.get("chance"))

    if tone == "good":
        base = 100.0
    elif tone == "warn":
        base = 62.0
    elif tone == "wait":
        base = max(5.0, 45.0 - min(_wl_number(status), 40))
    elif tone == "bad":
        base = 0.0
    else:
        base = 20.0

    if chance is not None:
        # Blend API confirmation % when present
        base = max(base, min(100.0, chance))
    return base


def score_leg_availability(leg: dict, preferred: Optional[list[str]] = None) -> float:
    """Best class score on one leg (optionally limited to preferred classes)."""
    if not leg or not leg.get("ok"):
        return -1.0
    if leg.get("departed"):
        return 0.0
    rows = filter_classes(leg.get("classes") or [], preferred)
    if not rows:
        return -1.0
    return max(score_class_row(r) for r in rows)


def best_fare_for_leg(leg: dict, preferred: Optional[list[str]] = None) -> Optional[float]:
    rows = filter_classes((leg or {}).get("classes") or [], preferred)
    fares = [f for f in (_parse_fare(r.get("fare")) for r in rows) if f is not None]
    return min(fares) if fares else None


def score_connection_seats(seat_data: dict, preferred: Optional[list[str]] = None) -> dict:
    """
    Score a two-leg seat check.
    Bottleneck = worse of the two legs (you need seats on BOTH).
    """
    leg1 = (seat_data or {}).get("leg1") or {}
    leg2 = (seat_data or {}).get("leg2") or {}
    s1 = score_leg_availability(leg1, preferred)
    s2 = score_leg_availability(leg2, preferred)
    f1 = best_fare_for_leg(leg1, preferred)
    f2 = best_fare_for_leg(leg2, preferred)
    total_fare = None
    if f1 is not None and f2 is not None:
        total_fare = f1 + f2
    elif f1 is not None:
        total_fare = f1
    elif f2 is not None:
        total_fare = f2

    if s1 < 0 and s2 < 0:
        bottleneck = -1.0
    elif s1 < 0:
        bottleneck = s2 * 0.5
    elif s2 < 0:
        bottleneck = s1 * 0.5
    else:
        bottleneck = min(s1, s2)

    return {
        "seat_score": round(bottleneck, 1),
        "leg1_score": round(s1, 1),
        "leg2_score": round(s2, 1),
        "est_fare": total_fare,
    }
