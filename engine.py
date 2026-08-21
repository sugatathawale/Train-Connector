import re
import datetime
import pandas as pd

# =====================================================================================
# DATA FILES
# =====================================================================================
# stations.csv                  -> station_code, station_name
# train_schedule_scappped.csv   -> train_number, train_name, day, station_name,
#                                   station_code (often BLANK), arrival, departure
# running_days_scrapped.csv     -> train_number, train_name, source, destination,
#                                   running_days  (7-digit string, Mon..Sun,
#                                   '1' = runs that day. Leading zeros get eaten if
#                                   the column is read as a number, e.g. "0000010"
#                                   becomes "10" -> we zfill(7) it back.)
# =====================================================================================

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]  # index 0 = Monday

# Booking tags sometimes get scraped into the station name (not real stations).
_BOOKING_TAG_RE = re.compile(r"\s*\((?:PQ|RL)\)\s*", re.IGNORECASE)

# Major metro / terminus clusters — used by "include nearby stations".
# Each code maps to the full cluster it belongs to (including itself).
_STATION_CLUSTERS = [
    # Delhi NCR
    {"NDLS", "DLI", "NZM", "ANVT", "DEE", "DEC", "DSJ", "GZB"},
    # Mumbai
    {"CSMT", "LTT", "BDTS", "MMCT", "DDR", "TNA", "KYN"},
    # Kolkata
    {"HWH", "KOAA", "SDAH"},
    # Chennai
    {"MAS", "MS"},
    # Bengaluru
    {"SBC", "YPR", "SMVB", "BNC"},
    # Hyderabad
    {"HYB", "SC", "KCG"},
]

_CLUSTER_LOOKUP = {}
for _cluster in _STATION_CLUSTERS:
    for _code in _cluster:
        _CLUSTER_LOOKUP[_code] = _cluster


def get_nearby_station_codes(station_code: str, include_nearby: bool = True) -> list:
    """
    Return station codes to search for a given origin/destination.
    With include_nearby=False (or unknown code), returns [station_code] only.
    """
    code = (station_code or "").strip().upper()
    if not code:
        return []
    if not include_nearby:
        return [code]
    cluster = _CLUSTER_LOOKUP.get(code)
    if not cluster:
        return [code]
    # Prefer the selected code first, then the rest alphabetically
    others = sorted(c for c in cluster if c != code)
    return [code] + others


def station_label(station_code: str) -> str:
    """'NDLS' -> 'NEW DELHI (NDLS)' using master names when possible."""
    code = (station_code or "").strip().upper()
    if not code:
        return ""
    name = CODE_TO_NAME.get(code) or code
    return f"{name} ({code})"


def nearby_station_labels(station_code: str) -> list:
    """Human-readable nearby options for captions / UI hints."""
    return [station_label(c) for c in get_nearby_station_codes(station_code, include_nearby=True)]


def clean_station_name(name: str) -> str:
    """
    Strip scraped booking tags like (PQ)/(RL) and tidy whitespace.
    'Jodhpur Jn (PQ)' -> 'Jodhpur Jn'
    """
    if name is None:
        return ""
    s = str(name).strip()
    # Remove tags repeatedly in case of odd spacing
    while True:
        cleaned = _BOOKING_TAG_RE.sub(" ", s)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned == s:
            break
        s = cleaned
    return s


def normalize_name(name: str) -> str:
    """Uppercase + collapse whitespace so station names from different files can be matched."""
    if name is None:
        return ""
    return re.sub(r"\s+", " ", clean_station_name(name).upper())


def _load_stations():
    """Build a NAME -> CODE lookup from stations.csv."""
    stations_df = pd.read_csv("stations.csv", dtype=str)
    stations_df.columns = [c.strip() for c in stations_df.columns]
    stations_df["station_code"] = stations_df["station_code"].fillna("").str.strip().str.upper()
    stations_df["station_name"] = stations_df["station_name"].fillna("").str.strip().map(clean_station_name)
    stations_df["norm_name"] = stations_df["station_name"].apply(normalize_name)

    # Keep first code seen per normalized name (in case of duplicate rows).
    name_to_code = (
        stations_df.drop_duplicates(subset="norm_name", keep="first")
        .set_index("norm_name")["station_code"]
        .to_dict()
    )
    code_to_name = (
        stations_df.drop_duplicates(subset="station_code", keep="first")
        .set_index("station_code")["station_name"]
        .to_dict()
    )
    used_codes = set(stations_df["station_code"].dropna().unique())
    return name_to_code, code_to_name, used_codes


NAME_TO_CODE, CODE_TO_NAME, _USED_CODES = _load_stations()
_FALLBACK_CACHE = {}  # norm_name -> generated code, so the same unmatched name always
                       # resolves to the SAME synthetic code (needed for intersections to work)


def _make_fallback_code(name: str) -> str:
    """
    If a station name in the schedule file can't be found in stations.csv, invent a
    stable code for it so trains stopping at the "same" unmatched station can still
    be recognised as a valid interchange point.
    """
    base = re.sub(r"[^A-Z]", "", normalize_name(name))[:4] or "STN"
    code = base
    i = 1
    while code in _USED_CODES:
        i += 1
        code = f"{base}{i}"
    _USED_CODES.add(code)
    return code


def _lookup_code_by_name(station_name: str) -> str:
    """Match cleaned/normalized name against the master station list."""
    norm = normalize_name(station_name)
    if not norm:
        return ""
    code = NAME_TO_CODE.get(norm, "")
    if code:
        return code
    # Light fuzzy: treat JN / JUNCTION the same
    alt = norm.replace(" JUNCTION", " JN").replace(" JN.", " JN")
    if alt != norm:
        code = NAME_TO_CODE.get(alt, "")
        if code:
            return code
    return ""


def _resolve_station_code(row):
    """
    Prefer the official code from stations.csv (by cleaned name).
    Only keep a scraped code if we cannot match the name.
    """
    cleaned_name = clean_station_name(row.get("station_name") or "")
    by_name = _lookup_code_by_name(cleaned_name)
    if by_name:
        return by_name

    existing = (row.get("station_code") or "").strip().upper()
    if existing and existing in CODE_TO_NAME:
        return existing
    if existing:
        return existing

    norm = normalize_name(cleaned_name)
    if norm not in _FALLBACK_CACHE:
        _FALLBACK_CACHE[norm] = _make_fallback_code(cleaned_name or "STN")
    return _FALLBACK_CACHE[norm]


def _display_station_name(code: str, fallback_name: str = "") -> str:
    """One clean display name per station code (prefer stations.csv)."""
    official = CODE_TO_NAME.get(code, "")
    name = clean_station_name(official or fallback_name)
    return name


def _build_station_list(schedule_df) -> list:
    """
    One dropdown entry per station code.
    Avoids duplicates from casing / (PQ) / (RL) name variants.
    """
    best = {}  # code -> display name
    for code, name in zip(schedule_df["station_code"], schedule_df["station_name"]):
        code = (code or "").strip().upper()
        if not code:
            continue
        display = _display_station_name(code, name)
        if not display:
            continue
        # Prefer official master name when available
        if code not in best or code in CODE_TO_NAME:
            best[code] = _display_station_name(code, display)

    return sorted(
        f"{name} ({code})"
        for code, name in best.items()
        if name and code
    )


def _load_schedule():
    df = pd.read_csv("train_schedule_scrapped.csv", dtype=str)
    df.columns = [c.strip() for c in df.columns]

    for col in ["train_number", "train_name", "station_name", "station_code", "arrival", "departure"]:
        if col not in df.columns:
            df[col] = ""
        df[col] = df[col].fillna("")

    df["train_number"] = df["train_number"].str.strip()
    # Clean names first so (PQ)/(RL)/casing variants map to the same station
    df["station_name"] = df["station_name"].map(clean_station_name)
    df["arrival"] = df["arrival"].replace("", "00:00:00")
    df["departure"] = df["departure"].replace("", "00:00:00")

    # Resolve/repair station codes (many rows arrive with a blank station_code).
    df["station_code"] = df.apply(_resolve_station_code, axis=1)
    # Canonical display name from master list when possible
    df["station_name"] = [
        _display_station_name(code, name)
        for code, name in zip(df["station_code"], df["station_name"])
    ]

    df["day"] = pd.to_numeric(df["day"], errors="coerce").fillna(1).astype(int)
    df["stop_seq"] = df.groupby("train_number").cumcount()

    df["abs_arrival"] = pd.to_timedelta(df["day"] - 1, unit="D") + pd.to_timedelta(df["arrival"])
    df["abs_departure"] = pd.to_timedelta(df["day"] - 1, unit="D") + pd.to_timedelta(df["departure"])
    return df


def _load_running_days():
    rd = pd.read_csv("running_days_scrapped.csv", dtype=str)
    rd.columns = [c.strip() for c in rd.columns]
    for col in ["train_number", "train_name", "source", "destination", "running_days"]:
        if col not in rd.columns:
            rd[col] = ""
        rd[col] = rd[col].fillna("")

    rd["train_number"] = rd["train_number"].str.strip()

    def fix_days(val):
        val = re.sub(r"[^01]", "", str(val))  # keep only 0/1 chars
        if not val:
            return ""  # unknown
        return val[-7:].zfill(7)  # restore eaten leading zeros, cap at 7 chars

    rd["running_days"] = rd["running_days"].apply(fix_days)
    return rd


# --- Load everything once (module-level, so it's cached for the life of the process) ---
df2 = _load_schedule()
_running_days_df = _load_running_days()

RUNNING_DAYS_MAP = dict(zip(_running_days_df["train_number"], _running_days_df["running_days"]))
TRAIN_META = _running_days_df.set_index("train_number")[["train_name", "source", "destination"]].to_dict("index")

# Dropdown list: one entry per station code — "STATION NAME (CODE)"
station_list = _build_station_list(df2)
# Searchable index of trains: (train_number, train_name) for the "lookup by train number" feature.
_first_rows = df2.drop_duplicates(subset="train_number")[["train_number", "train_name"]]
TRAIN_INDEX = sorted(
    {
        (row.train_number, (row.train_name or TRAIN_META.get(row.train_number, {}).get("train_name", "")).strip())
        for row in _first_rows.itertuples()
        if row.train_number
    },
    key=lambda x: x[0],
)


# =====================================================================================
# RUNNING-DAYS HELPERS
# =====================================================================================
def running_days_text(train_number: str) -> str:
    code = RUNNING_DAYS_MAP.get(str(train_number), "")
    if not code:
        return "Unknown"
    active = [WEEKDAYS[i] for i, c in enumerate(code) if c == "1"]
    if len(active) == 7:
        return "Daily"
    if not active:
        return "Unknown"
    return ", ".join(active)


def train_runs_on(train_number: str, weekday_idx: int) -> bool:
    """
    True if the train runs on the given weekday (0=Mon..6=Sun).
    If we have no running-day data for this train, we ASSUME it runs (so we don't
    silently hide valid connections just because the days file didn't cover it).
    """
    code = RUNNING_DAYS_MAP.get(str(train_number), "")
    if not code:
        return True
    return code[weekday_idx % 7] == "1"


def origin_weekday_for_boarding(boarding_weekday: int, schedule_day: int) -> int:
    """
    Running-days apply to the train's ORIGIN departure day.

    schedule_day is the CSV journey day at the boarding station (1 = origin day).
    Example: train leaves Chennai Sat (origin), Badnera is day 2 → boarding Sun
    means origin weekday = Sun - 1 = Sat.
    """
    offset = max(int(schedule_day or 1) - 1, 0)
    return (int(boarding_weekday) - offset) % 7


def train_runs_on_boarding_date(train_number: str, boarding_date, schedule_day: int) -> bool:
    """True if boarding on boarding_date at a stop on schedule_day is valid."""
    if boarding_date is None:
        return True
    origin_wd = origin_weekday_for_boarding(boarding_date.weekday(), schedule_day)
    return train_runs_on(train_number, origin_wd)


# =====================================================================================
# SMALL HELPERS
# =====================================================================================
def format_time(t) -> str:
    """'23:40:00' -> '23:40' for cleaner UI."""
    s = str(t or "").strip()
    if not s:
        return ""
    parts = s.split(":")
    if len(parts) >= 2:
        return f"{parts[0].zfill(2)}:{parts[1].zfill(2)}"
    return s


def format_duration(hours) -> str:
    """3.5 -> '3h 30m'."""
    try:
        h = float(hours)
    except (TypeError, ValueError):
        return ""
    total_min = int(round(h * 60))
    hh, mm = divmod(abs(total_min), 60)
    if hh and mm:
        return f"{hh}h {mm}m"
    if hh:
        return f"{hh}h"
    return f"{mm}m"


def comfort_label(layover_hrs: float) -> str:
    """Human-friendly layover quality for UI badges."""
    if layover_hrs < 1.0:
        return "Tight"
    if layover_hrs <= 3.0:
        return "Comfortable"
    if layover_hrs <= 6.0:
        return "Relaxed"
    if layover_hrs <= 12.0:
        return "Long wait"
    return "Overnight+"


def is_overnight_layover(arr_at_c: str, dep_from_c: str) -> bool:
    """True when connection crosses midnight or sits through deep night hours."""
    try:
        arr_h = int(str(arr_at_c).split(":")[0])
        dep_h = int(str(dep_from_c).split(":")[0])
    except (ValueError, IndexError):
        return False
    # Next-day connection, or both sides in awkward late/early window
    if dep_h < arr_h:
        return True
    return arr_h >= 22 or dep_h <= 5


def irctc_train_url(train_number: str) -> str:
    """Public IRCTC train-schedule search deep link (opens in browser)."""
    tn = str(train_number).strip()
    return f"https://www.irctc.co.in/nget/train-search?trainNumber={tn}"


def get_data_stats():
    """Coverage stats for the About / data tab."""
    return {
        "trains": int(df2["train_number"].nunique()),
        "schedule_rows": int(len(df2)),
        "stations_in_schedule": int(df2["station_code"].nunique()),
        "stations_master": len(NAME_TO_CODE),
        "running_days_known": sum(1 for v in RUNNING_DAYS_MAP.values() if v),
        "running_days_unknown": sum(1 for v in RUNNING_DAYS_MAP.values() if not v)
        + max(0, int(df2["train_number"].nunique()) - len(RUNNING_DAYS_MAP)),
    }


# =====================================================================================
# CORE SEARCH — CONNECTING TRAINS
# =====================================================================================
def find_connections_pro(
    start_code,
    end_code,
    via_code=None,
    min_layover_hrs=1,
    max_layover_hrs=12,
    pref_dep_after=None,
    pref_dep_before=None,
    pref_arr_after=None,
    pref_arr_before=None,
    search_date=None,          # datetime.date or None
    flexible_days=0,           # also try ±N days around search_date
    include_nearby=False,      # expand start/end to metro clusters
    sort_by="fastest",         # "fastest" | "layover" | "departure"
    exclude_overnight=False,
    max_results=None,
):
    start_codes = get_nearby_station_codes(start_code, include_nearby=include_nearby)
    end_codes = get_nearby_station_codes(end_code, include_nearby=include_nearby)
    # Same-metro pair (e.g. NDLS → NZM): don't expand both into one overlapping cluster
    if set(start_codes) & set(end_codes):
        start_codes = [(start_code or "").strip().upper()]
        end_codes = [(end_code or "").strip().upper()]
    end_codes = [c for c in end_codes if c and c not in start_codes]
    if not start_codes or not end_codes:
        return pd.DataFrame()

    # 1. LEG 1 (start -> mid)
    starts = df2[df2["station_code"].isin(start_codes)][
        ["train_number", "stop_seq", "abs_departure", "departure", "train_name", "day", "station_code"]
    ]
    starts = starts.rename(
        columns={
            "stop_seq": "start_seq",
            "abs_departure": "abs_start_dep",
            "departure": "start_time",
            "train_name": "train1_name",
            "day": "train1_start_day",
            "station_code": "start_from",
        }
    )

    if pref_dep_after is not None:
        starts = starts[starts["start_time"] >= pref_dep_after]
    if pref_dep_before is not None:
        starts = starts[starts["start_time"] <= pref_dep_before]

    leg1 = pd.merge(starts, df2, on="train_number")
    leg1 = leg1[leg1["stop_seq"] > leg1["start_seq"]]
    # Mid station must not be another "start" terminal we're treating as origin
    leg1 = leg1[~leg1["station_code"].isin(start_codes)]
    leg1["t1_duration_hrs"] = (leg1["abs_arrival"] - leg1["abs_start_dep"]).dt.total_seconds() / 3600
    leg1 = leg1[leg1["t1_duration_hrs"] > 0]

    leg1 = leg1[
        [
            "train_number",
            "train1_name",
            "station_code",
            "station_name",
            "start_time",
            "arrival",
            "t1_duration_hrs",
            "train1_start_day",
            "day",
            "start_from",
        ]
    ]
    leg1 = leg1.rename(
        columns={
            "train_number": "train1",
            "arrival": "arr_at_c",
            "station_code": "station_c",
            "start_time": "dep_from_a",
            "day": "train1_mid_day",
        }
    )

    # 2. LEG 2 (mid -> end)
    ends = df2[df2["station_code"].isin(end_codes)][
        ["train_number", "stop_seq", "abs_arrival", "arrival", "train_name", "station_code"]
    ]
    ends = ends.rename(
        columns={
            "stop_seq": "end_seq",
            "abs_arrival": "abs_end_arr",
            "arrival": "end_time",
            "train_name": "train2_name",
            "station_code": "end_at",
        }
    )

    if pref_arr_after is not None:
        ends = ends[ends["end_time"] >= pref_arr_after]
    if pref_arr_before is not None:
        ends = ends[ends["end_time"] <= pref_arr_before]

    leg2 = pd.merge(ends, df2, on="train_number")
    leg2 = leg2[leg2["stop_seq"] < leg2["end_seq"]]
    leg2 = leg2[~leg2["station_code"].isin(end_codes)]
    leg2["t2_duration_hrs"] = (leg2["abs_end_arr"] - leg2["abs_departure"]).dt.total_seconds() / 3600
    leg2 = leg2[leg2["t2_duration_hrs"] > 0]

    leg2 = leg2[
        [
            "train_number",
            "train2_name",
            "station_code",
            "departure",
            "end_time",
            "t2_duration_hrs",
            "day",
            "end_at",
        ]
    ]
    leg2 = leg2.rename(
        columns={
            "train_number": "train2",
            "departure": "dep_from_c",
            "station_code": "station_c",
            "end_time": "arr_at_b",
            "day": "train2_mid_day",
        }
    )

    # 3. INTERSECTIONS
    connections = pd.merge(leg1, leg2, on="station_c")
    connections = connections[connections["train1"] != connections["train2"]]
    connections = connections[connections["train1_name"] != connections["train2_name"]]

    if via_code is not None:
        connections = connections[connections["station_c"] == via_code]

    if connections.empty:
        return pd.DataFrame()

    # 4. MATH
    connections["arr_time"] = pd.to_timedelta(connections["arr_at_c"])
    connections["dep_time"] = pd.to_timedelta(connections["dep_from_c"])
    next_day_mask = connections["dep_time"] < connections["arr_time"]
    connections.loc[next_day_mask, "dep_time"] += pd.Timedelta(days=1)

    connections["layover_hours"] = (connections["dep_time"] - connections["arr_time"]).dt.total_seconds() / 3600
    connections["total_journey_hrs"] = (
        connections["t1_duration_hrs"] + connections["layover_hours"] + connections["t2_duration_hrs"]
    )
    connections["crosses_midnight"] = next_day_mask

    # 5. LAYOVER WINDOW
    valid_routes = connections[
        (connections["layover_hours"] >= min_layover_hrs)
        & (connections["layover_hours"] <= max_layover_hrs)
    ].copy()
    if valid_routes.empty:
        return pd.DataFrame()

    if exclude_overnight:
        overnight_mask = [
            is_overnight_layover(a, d) or bool(cm)
            for a, d, cm in zip(
                valid_routes["arr_at_c"], valid_routes["dep_from_c"], valid_routes["crosses_midnight"]
            )
        ]
        valid_routes = valid_routes[[not x for x in overnight_mask]]
        if valid_routes.empty:
            return pd.DataFrame()

    # 6. OPTIONAL: FILTER BY TRAVEL DATE (± flexible_days)
    if search_date is not None:
        flex = max(0, int(flexible_days or 0))
        candidate_dates = [
            search_date + datetime.timedelta(days=delta) for delta in range(-flex, flex + 1)
        ]

        kept_rows = []
        for row in valid_routes.itertuples(index=False):
            matching = []
            for d in candidate_dates:
                weekday0 = d.weekday()
                if not train_runs_on(
                    row.train1,
                    origin_weekday_for_boarding(weekday0, row.train1_start_day),
                ):
                    continue
                offset_days = int((row.t1_duration_hrs + row.layover_hours) // 24)
                mid_weekday = (weekday0 + offset_days) % 7
                if not train_runs_on(
                    row.train2,
                    origin_weekday_for_boarding(mid_weekday, row.train2_mid_day),
                ):
                    continue
                matching.append((d, offset_days))

            if not matching:
                continue
            best_date, best_offset = min(
                matching,
                key=lambda x: (abs((x[0] - search_date).days), x[0].toordinal()),
            )
            also_ok = ", ".join(
                d.strftime("%a %d %b") for d, _ in matching if d != best_date
            )
            kept_rows.append(
                {
                    "station_name": row.station_name,
                    "station_c": row.station_c,
                    "train1": row.train1,
                    "train1_name": row.train1_name,
                    "dep_from_a": row.dep_from_a,
                    "arr_at_c": row.arr_at_c,
                    "train2": row.train2,
                    "train2_name": row.train2_name,
                    "dep_from_c": row.dep_from_c,
                    "arr_at_b": row.arr_at_b,
                    "t1_duration_hrs": row.t1_duration_hrs,
                    "t2_duration_hrs": row.t2_duration_hrs,
                    "layover_hours": row.layover_hours,
                    "total_journey_hrs": row.total_journey_hrs,
                    "crosses_midnight": row.crosses_midnight,
                    "train2_day_offset": best_offset,
                    "start_from": row.start_from,
                    "end_at": row.end_at,
                    "boarding_date": best_date,
                    "available_dates": also_ok,
                }
            )

        valid_routes = pd.DataFrame(kept_rows)
        if valid_routes.empty:
            return pd.DataFrame()
    else:
        valid_routes = valid_routes.copy()
        valid_routes["train2_day_offset"] = (
            (valid_routes["t1_duration_hrs"] + valid_routes["layover_hours"]) // 24
        ).astype(int)
        valid_routes["boarding_date"] = None
        valid_routes["available_dates"] = ""

    # 7. SORT + DEDUPE (best train1/train2 pair; keep preferred start/end when nearby)
    sort_col_map = {"fastest": "total_journey_hrs", "layover": "layover_hours", "departure": "dep_from_a"}
    sort_col = sort_col_map.get(sort_by, "total_journey_hrs")
    # Prefer exact requested stations when nearby search is on
    valid_routes["_start_pref"] = (valid_routes["start_from"] != start_code).astype(int)
    valid_routes["_end_pref"] = (valid_routes["end_at"] != end_code).astype(int)
    valid_routes = valid_routes.sort_values(
        ["_start_pref", "_end_pref", sort_col]
    )
    valid_routes = valid_routes.drop_duplicates(subset=["train1", "train2"], keep="first")

    # 8. OUTPUT
    final_output = valid_routes[
        [
            "station_name",
            "station_c",
            "train1",
            "train1_name",
            "dep_from_a",
            "arr_at_c",
            "train2",
            "train2_name",
            "dep_from_c",
            "arr_at_b",
            "t1_duration_hrs",
            "t2_duration_hrs",
            "layover_hours",
            "total_journey_hrs",
            "crosses_midnight",
            "train2_day_offset",
            "start_from",
            "end_at",
            "boarding_date",
            "available_dates",
        ]
    ].copy()

    final_output["Train_1_Running_Days"] = final_output["train1"].apply(running_days_text)
    final_output["Train_2_Running_Days"] = final_output["train2"].apply(running_days_text)
    final_output["Comfort"] = final_output["layover_hours"].apply(comfort_label)
    final_output["Overnight_Layover"] = [
        "Yes" if (cm or is_overnight_layover(a, d)) else "No"
        for a, d, cm in zip(
            final_output["arr_at_c"], final_output["dep_from_c"], final_output["crosses_midnight"]
        )
    ]
    final_output["Leg1_Hrs"] = final_output["t1_duration_hrs"].round(1)
    final_output["Leg2_Hrs"] = final_output["t2_duration_hrs"].round(1)
    final_output["Board_Date"] = final_output["boarding_date"].apply(
        lambda d: d.isoformat() if isinstance(d, datetime.date) else ""
    )
    final_output["Board_On"] = final_output["boarding_date"].apply(
        lambda d: d.strftime("%a %d %b %Y") if isinstance(d, datetime.date) else ""
    )
    final_output["Start_From"] = final_output["start_from"].apply(station_label)
    final_output["End_At"] = final_output["end_at"].apply(station_label)

    final_output = final_output.rename(
        columns={
            "station_name": "Via_Station",
            "station_c": "Via_Code",
            "train1": "Train_1_No",
            "train1_name": "Train_1_Name",
            "dep_from_a": "Leave_Start",
            "arr_at_c": "Arrive_Mid",
            "train2": "Train_2_No",
            "train2_name": "Train_2_Name",
            "dep_from_c": "Leave_Mid",
            "arr_at_b": "Arrive_End",
            "layover_hours": "Layover_Hrs",
            "total_journey_hrs": "Total_Hrs",
            "train2_day_offset": "Train2_Day_Offset",
            "available_dates": "Also_OK_On",
        }
    )

    for col in ["Leave_Start", "Arrive_Mid", "Leave_Mid", "Arrive_End"]:
        final_output[col] = final_output[col].apply(format_time)

    final_output["Layover_Hrs"] = final_output["Layover_Hrs"].round(1)
    final_output["Total_Hrs"] = final_output["Total_Hrs"].round(1)

    display_cols = [
        "Start_From",
        "End_At",
        "Via_Station",
        "Via_Code",
        "Train_1_No",
        "Train_1_Name",
        "Leave_Start",
        "Arrive_Mid",
        "Leg1_Hrs",
        "Train_2_No",
        "Train_2_Name",
        "Leave_Mid",
        "Arrive_End",
        "Leg2_Hrs",
        "Layover_Hrs",
        "Total_Hrs",
        "Comfort",
        "Overnight_Layover",
        "Train_1_Running_Days",
        "Train_2_Running_Days",
        "Train2_Day_Offset",
        "Board_On",
        "Board_Date",
        "Also_OK_On",
    ]
    final_output = final_output[display_cols]

    if search_date is None:
        final_output = final_output.drop(
            columns=["Board_On", "Board_Date", "Also_OK_On"],
            errors="ignore",
        )
    elif int(flexible_days or 0) <= 0:
        final_output = final_output.drop(columns=["Also_OK_On"], errors="ignore")

    # Keep Start_From / End_At only when nearby search actually used alternate terminals
    if "Start_From" in final_output.columns and "End_At" in final_output.columns:
        if final_output["Start_From"].nunique() <= 1 and final_output["End_At"].nunique() <= 1:
            final_output = final_output.drop(columns=["Start_From", "End_At"], errors="ignore")

    out_sort_col = {
        "fastest": "Total_Hrs",
        "layover": "Layover_Hrs",
        "departure": "Leave_Start",
    }.get(sort_by, "Total_Hrs")
    final_output = final_output.sort_values(out_sort_col).reset_index(drop=True)

    if max_results is not None and max_results > 0:
        final_output = final_output.head(int(max_results))

    return final_output


def get_possible_via_stations(start_code, end_code, include_nearby=False):
    """Return a short, route-specific list of 'NAME (CODE)' via-station options
    (instead of forcing the user to scroll every station in the country)."""
    if not start_code or not end_code or start_code == end_code:
        return []
    results = find_connections_pro(
        start_code,
        end_code,
        min_layover_hrs=0,
        max_layover_hrs=24,
        include_nearby=include_nearby,
    )
    if results.empty:
        return []
    pairs = results[["Via_Station", "Via_Code"]].drop_duplicates()
    options = sorted(f"{row.Via_Station} ({row.Via_Code})" for row in pairs.itertuples())
    return options


# =====================================================================================
# DIRECT TRAINS (no change of train)
# =====================================================================================
def find_direct_trains(
    start_code,
    end_code,
    pref_dep_after=None,
    pref_dep_before=None,
    pref_arr_after=None,
    pref_arr_before=None,
    search_date=None,
    flexible_days=0,
    include_nearby=False,
    sort_by="fastest",
):
    """
    Trains that stop at start then later at end — no interchange needed.

    search_date = date you board at START station (not necessarily the origin day).
    flexible_days = also check ±N days around search_date (0 = exact date only).
    include_nearby = also search metro-cluster alternate terminals.
    """
    start_codes = get_nearby_station_codes(start_code, include_nearby=include_nearby)
    end_codes = get_nearby_station_codes(end_code, include_nearby=include_nearby)
    if set(start_codes) & set(end_codes):
        start_codes = [(start_code or "").strip().upper()]
        end_codes = [(end_code or "").strip().upper()]
    end_codes = [c for c in end_codes if c and c not in start_codes]
    if not start_codes or not end_codes:
        return pd.DataFrame()

    starts = df2[df2["station_code"].isin(start_codes)][
        ["train_number", "train_name", "stop_seq", "abs_departure", "departure", "day", "station_code"]
    ].rename(
        columns={
            "stop_seq": "start_seq",
            "abs_departure": "abs_start_dep",
            "departure": "dep_time",
            "day": "dep_day",
            "station_code": "start_from",
        }
    )
    ends = df2[df2["station_code"].isin(end_codes)][
        ["train_number", "stop_seq", "abs_arrival", "arrival", "day", "station_code"]
    ].rename(
        columns={
            "stop_seq": "end_seq",
            "abs_arrival": "abs_end_arr",
            "arrival": "arr_time",
            "day": "arr_day",
            "station_code": "end_at",
        }
    )

    merged = pd.merge(starts, ends, on="train_number")
    merged = merged[merged["end_seq"] > merged["start_seq"]]
    merged["duration_hrs"] = (merged["abs_end_arr"] - merged["abs_start_dep"]).dt.total_seconds() / 3600
    merged = merged[merged["duration_hrs"] > 0]

    if pref_dep_after is not None:
        merged = merged[merged["dep_time"] >= pref_dep_after]
    if pref_dep_before is not None:
        merged = merged[merged["dep_time"] <= pref_dep_before]
    if pref_arr_after is not None:
        merged = merged[merged["arr_time"] >= pref_arr_after]
    if pref_arr_before is not None:
        merged = merged[merged["arr_time"] <= pref_arr_before]

    if search_date is not None and not merged.empty:
        flex = max(0, int(flexible_days or 0))
        candidate_dates = [
            search_date + datetime.timedelta(days=delta) for delta in range(-flex, flex + 1)
        ]

        kept_rows = []
        for row in merged.itertuples(index=False):
            matching_dates = [
                d
                for d in candidate_dates
                if train_runs_on_boarding_date(row.train_number, d, row.dep_day)
            ]
            if not matching_dates:
                continue
            # Prefer the exact travel date, else nearest day
            best = min(
                matching_dates,
                key=lambda d: (abs((d - search_date).days), d.toordinal()),
            )
            origin_wd = origin_weekday_for_boarding(best.weekday(), row.dep_day)
            kept_rows.append(
                {
                    "train_number": row.train_number,
                    "train_name": row.train_name,
                    "dep_time": row.dep_time,
                    "arr_time": row.arr_time,
                    "dep_day": row.dep_day,
                    "arr_day": row.arr_day,
                    "duration_hrs": row.duration_hrs,
                    "start_seq": row.start_seq,
                    "end_seq": row.end_seq,
                    "start_from": row.start_from,
                    "end_at": row.end_at,
                    "boarding_date": best,
                    "available_dates": ", ".join(
                        d.strftime("%a %d %b") for d in matching_dates
                    ),
                    "leaves_origin_on": WEEKDAYS[origin_wd],
                }
            )

        merged = pd.DataFrame(kept_rows)
    else:
        if not merged.empty:
            merged = merged.copy()
            merged["boarding_date"] = None
            merged["available_dates"] = ""
            merged["leaves_origin_on"] = ""

    if merged.empty:
        return pd.DataFrame()

    # Prefer exact requested terminals, then fastest
    merged["_start_pref"] = (merged["start_from"] != start_code).astype(int)
    merged["_end_pref"] = (merged["end_at"] != end_code).astype(int)
    merged = merged.sort_values(["_start_pref", "_end_pref", "duration_hrs"]).drop_duplicates(
        subset=["train_number"], keep="first"
    )
    merged["Running_Days"] = merged["train_number"].apply(running_days_text)
    merged["Stops_Between"] = (merged["end_seq"] - merged["start_seq"] - 1).astype(int)

    out_cols = [
        "train_number",
        "train_name",
        "dep_time",
        "arr_time",
        "dep_day",
        "arr_day",
        "duration_hrs",
        "Stops_Between",
        "Running_Days",
        "leaves_origin_on",
        "boarding_date",
        "available_dates",
        "start_from",
        "end_at",
    ]
    out = merged[out_cols].copy()
    out["dep_time"] = out["dep_time"].apply(format_time)
    out["arr_time"] = out["arr_time"].apply(format_time)
    out["duration_hrs"] = out["duration_hrs"].round(1)
    out["board_date_iso"] = out["boarding_date"].apply(
        lambda d: d.isoformat() if isinstance(d, datetime.date) else ""
    )
    out["boarding_date"] = out["boarding_date"].apply(
        lambda d: d.strftime("%a %d %b %Y") if isinstance(d, datetime.date) else ""
    )
    out["Start_From"] = out["start_from"].apply(station_label)
    out["End_At"] = out["end_at"].apply(station_label)
    out = out.rename(
        columns={
            "train_number": "Train_No",
            "train_name": "Train_Name",
            "dep_time": "Departure",
            "arr_time": "Arrival",
            "dep_day": "Journey_Day_At_Start",
            "arr_day": "Journey_Day_At_End",
            "duration_hrs": "Duration_Hrs",
            "leaves_origin_on": "Leaves_Origin_On",
            "boarding_date": "Board_On",
            "available_dates": "Also_OK_On",
            "board_date_iso": "Board_Date",
        }
    )
    out = out.drop(columns=["start_from", "end_at"], errors="ignore")

    sort_map = {"fastest": "Duration_Hrs", "departure": "Departure", "layover": "Duration_Hrs"}
    out = out.sort_values(sort_map.get(sort_by, "Duration_Hrs")).reset_index(drop=True)

    # Hide empty flexible-date columns when not used
    if search_date is None:
        out = out.drop(
            columns=["Board_On", "Also_OK_On", "Leaves_Origin_On", "Board_Date"],
            errors="ignore",
        )
    elif int(flexible_days or 0) <= 0:
        out = out.drop(columns=["Also_OK_On"], errors="ignore")

    if not include_nearby:
        same_start = out["Start_From"].nunique() <= 1 if "Start_From" in out.columns else True
        same_end = out["End_At"].nunique() <= 1 if "End_At" in out.columns else True
        if same_start and same_end:
            out = out.drop(columns=["Start_From", "End_At"], errors="ignore")
    elif "Start_From" in out.columns and "End_At" in out.columns:
        if out["Start_From"].nunique() <= 1 and out["End_At"].nunique() <= 1:
            out = out.drop(columns=["Start_From", "End_At"], errors="ignore")

    return out


# =====================================================================================
# STATION EXPLORER
# =====================================================================================
def get_trains_at_station(station_code, time_after=None, time_before=None):
    """All trains that stop at a station, with arrival/departure and running days."""
    if not station_code:
        return pd.DataFrame()

    stops = df2[df2["station_code"] == station_code].copy()
    if stops.empty:
        return pd.DataFrame()

    if time_after is not None:
        stops = stops[stops["departure"] >= time_after]
    if time_before is not None:
        stops = stops[stops["departure"] <= time_before]

    if stops.empty:
        return pd.DataFrame()

    # Prefer the earliest stop if a train somehow appears twice
    stops = stops.sort_values(["train_number", "stop_seq"]).drop_duplicates(
        subset=["train_number"], keep="first"
    )

    rows = []
    for row in stops.itertuples():
        meta = TRAIN_META.get(row.train_number, {})
        full = df2[df2["train_number"] == row.train_number].sort_values("stop_seq")
        origin = meta.get("source") or (full["station_name"].iloc[0] if len(full) else "")
        dest = meta.get("destination") or (full["station_name"].iloc[-1] if len(full) else "")
        rows.append(
            {
                "Train_No": row.train_number,
                "Train_Name": row.train_name or meta.get("train_name", ""),
                "Arrival": format_time(row.arrival),
                "Departure": format_time(row.departure),
                "Day": int(row.day),
                "From": origin,
                "To": dest,
                "Running_Days": running_days_text(row.train_number),
            }
        )

    out = pd.DataFrame(rows)
    return out.sort_values("Departure").reset_index(drop=True)


def get_top_via_hubs(start_code, end_code, limit=15, include_nearby=False):
    """Most common interchange stations for a route (by number of valid connections)."""
    results = find_connections_pro(
        start_code,
        end_code,
        min_layover_hrs=0.5,
        max_layover_hrs=18,
        include_nearby=include_nearby,
    )
    if results.empty:
        return pd.DataFrame()
    hubs = (
        results.groupby(["Via_Station", "Via_Code"], as_index=False)
        .agg(
            Connections=("Train_1_No", "count"),
            Best_Total_Hrs=("Total_Hrs", "min"),
            Best_Layover=("Layover_Hrs", "min"),
        )
        .sort_values(["Connections", "Best_Total_Hrs"], ascending=[False, True])
        .head(limit)
        .reset_index(drop=True)
    )
    return hubs


# =====================================================================================
# TRAIN LOOKUP (search by number / name, view full schedule + running days)
# =====================================================================================
def search_trains(query, limit=25):
    """Match a typed query against train number (prefix/contains) or train name (contains)."""
    q = str(query).strip().upper()
    if not q:
        return []
    matches = [(tn, name) for tn, name in TRAIN_INDEX if q in tn.upper() or q in (name or "").upper()]
    # Prioritise exact / prefix train-number matches first.
    matches.sort(key=lambda x: (not x[0].upper().startswith(q), x[0]))
    return matches[:limit]


def get_train_schedule(train_number):
    """Full stop-by-stop schedule for one train, in a UI-friendly format."""
    train_number = str(train_number).strip()
    sched = df2[df2["train_number"] == train_number].sort_values("stop_seq").copy()
    if sched.empty:
        return pd.DataFrame()

    sched["halt_minutes"] = ((sched["abs_departure"] - sched["abs_arrival"]).dt.total_seconds() / 60).round(0)
    sched.loc[sched["halt_minutes"] < 0, "halt_minutes"] = 0

    out = sched[["stop_seq", "day", "station_name", "station_code", "arrival", "departure", "halt_minutes"]].copy()
    out["stop_seq"] = out["stop_seq"] + 1
    out["arrival"] = out["arrival"].apply(format_time)
    out["departure"] = out["departure"].apply(format_time)
    out = out.rename(
        columns={
            "stop_seq": "Stop #",
            "day": "Day",
            "station_name": "Station",
            "station_code": "Code",
            "arrival": "Arrival",
            "departure": "Departure",
            "halt_minutes": "Halt (min)",
        }
    )
    return out.reset_index(drop=True)


def get_running_days_bits(train_number):
    return RUNNING_DAYS_MAP.get(str(train_number).strip(), "")


def get_train_summary(train_number):
    """High-level info card for a train: name, source/destination, running days, duration."""
    train_number = str(train_number).strip()
    raw = df2[df2["train_number"] == train_number].sort_values("stop_seq")
    if raw.empty:
        return None

    meta = TRAIN_META.get(train_number, {})
    name = meta.get("train_name") or raw["train_name"].iloc[0] or "Unknown"
    source = meta.get("source") or raw["station_name"].iloc[0]
    destination = meta.get("destination") or raw["station_name"].iloc[-1]
    duration_hrs = round((raw["abs_arrival"].iloc[-1] - raw["abs_departure"].iloc[0]).total_seconds() / 3600, 1)

    return {
        "train_number": train_number,
        "train_name": name,
        "source": source,
        "destination": destination,
        "running_days_text": running_days_text(train_number),
        "running_days_bits": get_running_days_bits(train_number),
        "total_stops": len(raw),
        "total_duration_hrs": duration_hrs,
        "irctc_url": irctc_train_url(train_number),
    }
