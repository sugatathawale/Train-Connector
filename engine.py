import re
import datetime
from typing import Optional

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
    type_counts = {}
    for _, name in TRAIN_INDEX:
        t = classify_train_type(name)
        type_counts[t] = type_counts.get(t, 0) + 1
    return {
        "trains": int(df2["train_number"].nunique()),
        "schedule_rows": int(len(df2)),
        "stations_in_schedule": int(df2["station_code"].nunique()),
        "stations_master": len(NAME_TO_CODE),
        "running_days_known": sum(1 for v in RUNNING_DAYS_MAP.values() if v),
        "running_days_unknown": sum(1 for v in RUNNING_DAYS_MAP.values() if not v)
        + max(0, int(df2["train_number"].nunique()) - len(RUNNING_DAYS_MAP)),
        "train_types": dict(sorted(type_counts.items(), key=lambda x: (-x[1], x[0]))),
    }


# =====================================================================================
# TRAIN TYPE + HALT HELPERS
# =====================================================================================
TRAIN_TYPE_OPTIONS = [
    "Vande Bharat",
    "Rajdhani",
    "Shatabdi",
    "Duronto",
    "Tejas",
    "Humsafar",
    "Garib Rath",
    "Express",
    "Superfast",
    "Passenger",
    "Other",
]


def classify_train_type(train_name: str) -> str:
    """Best-effort type from train name (scraped names vary)."""
    n = (train_name or "").upper()
    if "VANDE" in n:
        return "Vande Bharat"
    if "RAJDHANI" in n or "RJDHNI" in n or "RAJDHNI" in n:
        return "Rajdhani"
    if "SHATABDI" in n:
        return "Shatabdi"
    if "DURONTO" in n:
        return "Duronto"
    if "TEJAS" in n:
        return "Tejas"
    if "HUMSAFAR" in n:
        return "Humsafar"
    if "GARIB" in n:
        return "Garib Rath"
    if "PASSENGER" in n or re.search(r"\bPASS\b", n):
        return "Passenger"
    if "SUPERFAST" in n or re.search(r"\bSF\b", n) or n.endswith(" SF"):
        return "Superfast"
    if "EXPRESS" in n or re.search(r"\bEXP\b", n) or n.endswith(" EXP"):
        return "Express"
    return "Other"


def _train_type_ok(train_name: str, allowed_types) -> bool:
    if not allowed_types:
        return True
    return classify_train_type(train_name) in set(allowed_types)


# (train_number, station_code) -> halt minutes
_HALT_CACHE = {}


def _halt_minutes_at(train_number: str, station_code: str) -> Optional[float]:
    key = (str(train_number), str(station_code).upper())
    if key in _HALT_CACHE:
        return _HALT_CACHE[key]
    rows = df2[
        (df2["train_number"] == str(train_number))
        & (df2["station_code"] == str(station_code).upper())
    ]
    if rows.empty:
        _HALT_CACHE[key] = None
        return None
    row = rows.iloc[0]
    mins = (row["abs_departure"] - row["abs_arrival"]).total_seconds() / 60.0
    if mins < 0:
        mins = 0.0
    _HALT_CACHE[key] = round(mins, 0)
    return _HALT_CACHE[key]


def change_warning_text(t1_halt, t2_halt, layover_hrs) -> str:
    """Human warning when the interchange looks tight or rushed."""
    notes = []
    try:
        lay = float(layover_hrs)
    except (TypeError, ValueError):
        lay = None
    if t2_halt is not None and t2_halt <= 2:
        notes.append(f"Train 2 only halts ~{int(t2_halt)} min — tight boarding")
    elif t2_halt is not None and t2_halt <= 5:
        notes.append(f"Train 2 short halt (~{int(t2_halt)} min)")
    if t1_halt is not None and t1_halt <= 2:
        notes.append(f"Train 1 short stop (~{int(t1_halt)} min)")
    if lay is not None and lay < 1.0:
        notes.append("Very short wait — delay risk")
    return " · ".join(notes)


def enrich_halt_warnings(results: pd.DataFrame) -> pd.DataFrame:
    """Add halt minutes + Change_Warning for one-change results."""
    if results is None or results.empty:
        return results
    out = results.copy()
    if "Via_Code" not in out.columns:
        return out
    t1_halts, t2_halts, warns = [], [], []
    for _, row in out.iterrows():
        via = str(row.get("Via_Code") or "")
        h1 = _halt_minutes_at(str(row.get("Train_1_No") or ""), via)
        h2 = _halt_minutes_at(str(row.get("Train_2_No") or ""), via)
        t1_halts.append(h1)
        t2_halts.append(h2)
        warns.append(change_warning_text(h1, h2, row.get("Layover_Hrs")))
    out["Train_1_Halt_Min"] = t1_halts
    out["Train_2_Halt_Min"] = t2_halts
    out["Change_Warning"] = warns
    return out


def _normalize_code_list(codes) -> list:
    if not codes:
        return []
    if isinstance(codes, str):
        codes = [codes]
    out = []
    for c in codes:
        c = (c or "").strip().upper()
        if c and c not in out:
            out.append(c)
    return out


def _top_outbound_hubs(origin_codes, exclude_codes=None, limit=10) -> list:
    """Stations most often reached after boarding at origin_codes."""
    origin_codes = _normalize_code_list(origin_codes)
    exclude = set(_normalize_code_list(exclude_codes)) | set(origin_codes)
    if not origin_codes:
        return []
    origins = df2[df2["station_code"].isin(origin_codes)][
        ["train_number", "stop_seq"]
    ].rename(columns={"stop_seq": "o_seq"})
    later = origins.merge(df2, on="train_number")
    later = later[later["stop_seq"] > later["o_seq"]]
    later = later[~later["station_code"].isin(exclude)]
    if later.empty:
        return []
    counts = later.groupby("station_code").size().sort_values(ascending=False)
    return list(counts.head(int(limit)).index)


def _top_inbound_hubs(dest_codes, exclude_codes=None, limit=10) -> list:
    """Stations most often left before arriving at dest_codes."""
    dest_codes = _normalize_code_list(dest_codes)
    exclude = set(_normalize_code_list(exclude_codes)) | set(dest_codes)
    if not dest_codes:
        return []
    dests = df2[df2["station_code"].isin(dest_codes)][
        ["train_number", "stop_seq"]
    ].rename(columns={"stop_seq": "d_seq"})
    earlier = dests.merge(df2, on="train_number")
    earlier = earlier[earlier["stop_seq"] < earlier["d_seq"]]
    earlier = earlier[~earlier["station_code"].isin(exclude)]
    if earlier.empty:
        return []
    counts = earlier.groupby("station_code").size().sort_values(ascending=False)
    return list(counts.head(int(limit)).index)
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
    train_types=None,          # list of classify_train_type labels
    avoid_via_codes=None,      # never change here
    prefer_via_codes=None,     # soft-prefer these hubs (sorted first)
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

    avoid_set = set(_normalize_code_list(avoid_via_codes))
    prefer_set = set(_normalize_code_list(prefer_via_codes))
    # Pin via_code still wins as hard filter later; prefer list is soft.
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
    if avoid_set:
        connections = connections[~connections["station_c"].isin(avoid_set)]
    # prefer_via_codes is soft — applied later via sort key _via_pref

    if train_types:
        t1_ok = connections["train1_name"].apply(lambda n: _train_type_ok(n, train_types))
        t2_ok = connections["train2_name"].apply(lambda n: _train_type_ok(n, train_types))
        connections = connections[t1_ok & t2_ok]

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
    valid_routes["_via_pref"] = (~valid_routes["station_c"].isin(prefer_set)).astype(int) if prefer_set else 0
    valid_routes = valid_routes.sort_values(
        ["_via_pref", "_start_pref", "_end_pref", sort_col]
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
    final_output["Train_1_Type"] = final_output["train1_name"].apply(classify_train_type)
    final_output["Train_2_Type"] = final_output["train2_name"].apply(classify_train_type)
    final_output["Changes"] = 1
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

    # Halt warnings at the interchange
    final_output["Train_1_Halt_Min"] = [
        _halt_minutes_at(t, v) for t, v in zip(final_output["train1"], final_output["station_c"])
    ]
    final_output["Train_2_Halt_Min"] = [
        _halt_minutes_at(t, v) for t, v in zip(final_output["train2"], final_output["station_c"])
    ]
    final_output["Change_Warning"] = [
        change_warning_text(h1, h2, lay)
        for h1, h2, lay in zip(
            final_output["Train_1_Halt_Min"],
            final_output["Train_2_Halt_Min"],
            final_output["layover_hours"],
        )
    ]

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
        "Changes",
        "Start_From",
        "End_At",
        "Via_Station",
        "Via_Code",
        "Train_1_No",
        "Train_1_Name",
        "Train_1_Type",
        "Leave_Start",
        "Arrive_Mid",
        "Leg1_Hrs",
        "Train_1_Halt_Min",
        "Train_2_No",
        "Train_2_Name",
        "Train_2_Type",
        "Leave_Mid",
        "Arrive_End",
        "Leg2_Hrs",
        "Train_2_Halt_Min",
        "Layover_Hrs",
        "Total_Hrs",
        "Comfort",
        "Change_Warning",
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


def find_two_change_connections(
    start_code,
    end_code,
    min_layover_hrs=1.0,
    max_layover_hrs=8.0,
    max_total_hrs=48.0,
    search_date=None,
    flexible_days=0,
    include_nearby=False,
    train_types=None,
    avoid_via_codes=None,
    hub_limit=6,
    max_results=25,
    sort_by="fastest",
):
    """
    Optional 2-change (3-train) routes: start → via1 → via2 → end.

    Hub candidates are capped (hub_limit) so the search stays tractable.
    """
    start_codes = get_nearby_station_codes(start_code, include_nearby=include_nearby)
    end_codes = get_nearby_station_codes(end_code, include_nearby=include_nearby)
    if set(start_codes) & set(end_codes):
        start_codes = [(start_code or "").strip().upper()]
        end_codes = [(end_code or "").strip().upper()]
    end_codes = [c for c in end_codes if c and c not in start_codes]
    if not start_codes or not end_codes:
        return pd.DataFrame()

    avoid_set = set(_normalize_code_list(avoid_via_codes))
    via1_list = [
        c
        for c in _top_outbound_hubs(start_codes, end_codes, limit=hub_limit)
        if c not in avoid_set
    ]
    via2_list = [
        c
        for c in _top_inbound_hubs(end_codes, start_codes, limit=hub_limit)
        if c not in avoid_set
    ]
    if not via1_list or not via2_list:
        return pd.DataFrame()

    # --- Leg A: start → via1 ---
    a_board = df2[df2["station_code"].isin(start_codes)][
        ["train_number", "train_name", "stop_seq", "abs_departure", "departure", "day", "station_code"]
    ].rename(
        columns={
            "stop_seq": "a_seq",
            "abs_departure": "a_abs_dep",
            "departure": "leave_start",
            "day": "train1_start_day",
            "station_code": "start_from",
            "train_name": "train1_name",
        }
    )
    leg_a = a_board.merge(df2, on="train_number")
    leg_a = leg_a[
        (leg_a["stop_seq"] > leg_a["a_seq"]) & (leg_a["station_code"].isin(via1_list))
    ]
    leg_a["t1_hrs"] = (leg_a["abs_arrival"] - leg_a["a_abs_dep"]).dt.total_seconds() / 3600
    leg_a = leg_a[leg_a["t1_hrs"] > 0]
    if train_types:
        leg_a = leg_a[leg_a["train1_name"].apply(lambda n: _train_type_ok(n, train_types))]
    leg_a = leg_a[
        [
            "train_number",
            "train1_name",
            "leave_start",
            "arrival",
            "t1_hrs",
            "train1_start_day",
            "day",
            "start_from",
            "station_code",
            "station_name",
        ]
    ].rename(
        columns={
            "train_number": "train1",
            "arrival": "arr_via1",
            "day": "train1_via1_day",
            "station_code": "via1",
            "station_name": "via1_name",
        }
    )
    if leg_a.empty:
        return pd.DataFrame()

    # --- Leg B: via1 → via2 ---
    b_board = df2[df2["station_code"].isin(via1_list)][
        ["train_number", "train_name", "stop_seq", "abs_departure", "departure", "day", "station_code"]
    ].rename(
        columns={
            "stop_seq": "b_seq",
            "abs_departure": "b_abs_dep",
            "departure": "leave_via1",
            "day": "train2_via1_day",
            "station_code": "via1",
            "train_name": "train2_name",
        }
    )
    leg_b = b_board.merge(df2, on="train_number")
    leg_b = leg_b[
        (leg_b["stop_seq"] > leg_b["b_seq"]) & (leg_b["station_code"].isin(via2_list))
    ]
    leg_b = leg_b[leg_b["via1"] != leg_b["station_code"]]
    leg_b["t2_hrs"] = (leg_b["abs_arrival"] - leg_b["b_abs_dep"]).dt.total_seconds() / 3600
    leg_b = leg_b[leg_b["t2_hrs"] > 0]
    if train_types:
        leg_b = leg_b[leg_b["train2_name"].apply(lambda n: _train_type_ok(n, train_types))]
    leg_b = leg_b[
        [
            "train_number",
            "train2_name",
            "leave_via1",
            "arrival",
            "t2_hrs",
            "train2_via1_day",
            "day",
            "via1",
            "station_code",
            "station_name",
        ]
    ].rename(
        columns={
            "train_number": "train2",
            "arrival": "arr_via2",
            "day": "train2_via2_day",
            "station_code": "via2",
            "station_name": "via2_name",
        }
    )
    if leg_b.empty:
        return pd.DataFrame()

    # --- Leg C: via2 → end ---
    c_arr = df2[df2["station_code"].isin(end_codes)][
        ["train_number", "train_name", "stop_seq", "abs_arrival", "arrival", "station_code"]
    ].rename(
        columns={
            "stop_seq": "c_seq",
            "abs_arrival": "c_abs_arr",
            "arrival": "arrive_end",
            "station_code": "end_at",
            "train_name": "train3_name",
        }
    )
    leg_c = c_arr.merge(df2, on="train_number")
    leg_c = leg_c[
        (leg_c["stop_seq"] < leg_c["c_seq"]) & (leg_c["station_code"].isin(via2_list))
    ]
    leg_c["t3_hrs"] = (leg_c["c_abs_arr"] - leg_c["abs_departure"]).dt.total_seconds() / 3600
    leg_c = leg_c[leg_c["t3_hrs"] > 0]
    if train_types:
        leg_c = leg_c[leg_c["train3_name"].apply(lambda n: _train_type_ok(n, train_types))]
    leg_c = leg_c[
        [
            "train_number",
            "train3_name",
            "departure",
            "arrive_end",
            "t3_hrs",
            "day",
            "station_code",
            "end_at",
        ]
    ].rename(
        columns={
            "train_number": "train3",
            "departure": "leave_via2",
            "day": "train3_via2_day",
            "station_code": "via2",
        }
    )
    if leg_c.empty:
        return pd.DataFrame()

    # Join A-B on via1, then B-C on via2
    ab = leg_a.merge(leg_b, on="via1")
    ab = ab[
        (ab["train1"] != ab["train2"])
        & (ab["train1_name"] != ab["train2_name"])
    ]
    if ab.empty:
        return pd.DataFrame()

    routes = ab.merge(leg_c, on="via2")
    routes = routes[
        (routes["train2"] != routes["train3"])
        & (routes["train3"] != routes["train1"])
        & (routes["train2_name"] != routes["train3_name"])
        & (routes["via1"] != routes["via2"])
    ]
    if routes.empty:
        return pd.DataFrame()

    # Layover 1 at via1
    routes["arr1"] = pd.to_timedelta(routes["arr_via1"])
    routes["dep1"] = pd.to_timedelta(routes["leave_via1"])
    cross1 = routes["dep1"] < routes["arr1"]
    routes.loc[cross1, "dep1"] += pd.Timedelta(days=1)
    routes["lay1"] = (routes["dep1"] - routes["arr1"]).dt.total_seconds() / 3600

    # Layover 2 at via2
    routes["arr2"] = pd.to_timedelta(routes["arr_via2"])
    routes["dep2"] = pd.to_timedelta(routes["leave_via2"])
    cross2 = routes["dep2"] < routes["arr2"]
    routes.loc[cross2, "dep2"] += pd.Timedelta(days=1)
    routes["lay2"] = (routes["dep2"] - routes["arr2"]).dt.total_seconds() / 3600

    routes["total_hrs"] = (
        routes["t1_hrs"] + routes["lay1"] + routes["t2_hrs"] + routes["lay2"] + routes["t3_hrs"]
    )
    routes = routes[
        (routes["lay1"] >= min_layover_hrs)
        & (routes["lay1"] <= max_layover_hrs)
        & (routes["lay2"] >= min_layover_hrs)
        & (routes["lay2"] <= max_layover_hrs)
        & (routes["total_hrs"] <= max_total_hrs)
    ]
    if routes.empty:
        return pd.DataFrame()

    # Date filter (train1 / train2 / train3 origin weekdays)
    if search_date is not None:
        flex = max(0, int(flexible_days or 0))
        candidate_dates = [
            search_date + datetime.timedelta(days=delta) for delta in range(-flex, flex + 1)
        ]
        kept = []
        for row in routes.itertuples(index=False):
            matching = []
            for d in candidate_dates:
                wd0 = d.weekday()
                if not train_runs_on(
                    row.train1, origin_weekday_for_boarding(wd0, row.train1_start_day)
                ):
                    continue
                off2 = int((row.t1_hrs + row.lay1) // 24)
                wd2 = (wd0 + off2) % 7
                if not train_runs_on(
                    row.train2, origin_weekday_for_boarding(wd2, row.train2_via1_day)
                ):
                    continue
                off3 = int((row.t1_hrs + row.lay1 + row.t2_hrs + row.lay2) // 24)
                wd3 = (wd0 + off3) % 7
                if not train_runs_on(
                    row.train3, origin_weekday_for_boarding(wd3, row.train3_via2_day)
                ):
                    continue
                matching.append((d, off2, off3))
            if not matching:
                continue
            best = min(matching, key=lambda x: (abs((x[0] - search_date).days), x[0].toordinal()))
            kept.append(
                {
                    "via1": row.via1,
                    "via1_name": row.via1_name,
                    "via2": row.via2,
                    "via2_name": row.via2_name,
                    "train1": row.train1,
                    "train1_name": row.train1_name,
                    "leave_start": row.leave_start,
                    "arr_via1": row.arr_via1,
                    "t1_hrs": row.t1_hrs,
                    "lay1": row.lay1,
                    "train2": row.train2,
                    "train2_name": row.train2_name,
                    "leave_via1": row.leave_via1,
                    "arr_via2": row.arr_via2,
                    "t2_hrs": row.t2_hrs,
                    "lay2": row.lay2,
                    "train3": row.train3,
                    "train3_name": row.train3_name,
                    "leave_via2": row.leave_via2,
                    "arrive_end": row.arrive_end,
                    "t3_hrs": row.t3_hrs,
                    "total_hrs": row.total_hrs,
                    "start_from": row.start_from,
                    "end_at": row.end_at,
                    "boarding_date": best[0],
                    "train2_day_offset": best[1],
                    "train3_day_offset": best[2],
                }
            )
        routes = pd.DataFrame(kept)
        if routes.empty:
            return pd.DataFrame()
    else:
        routes = routes.copy()
        routes["boarding_date"] = None
        routes["train2_day_offset"] = (routes["t1_hrs"] + routes["lay1"]).floordiv(24).astype(int)
        routes["train3_day_offset"] = (
            (routes["t1_hrs"] + routes["lay1"] + routes["t2_hrs"] + routes["lay2"])
            .floordiv(24)
            .astype(int)
        )

    routes = routes.sort_values("total_hrs").drop_duplicates(
        subset=["train1", "train2", "train3"], keep="first"
    )

    out = pd.DataFrame(
        {
            "Changes": 2,
            "Start_From": routes["start_from"].apply(station_label),
            "End_At": routes["end_at"].apply(station_label),
            "Via_Station": [
                f"{a} → {b}" for a, b in zip(routes["via1_name"], routes["via2_name"])
            ],
            "Via_Code": [
                f"{a}+{b}" for a, b in zip(routes["via1"], routes["via2"])
            ],
            "Via1_Code": routes["via1"].values,
            "Via2_Code": routes["via2"].values,
            "Train_1_No": routes["train1"].values,
            "Train_1_Name": routes["train1_name"].values,
            "Train_1_Type": [classify_train_type(n) for n in routes["train1_name"]],
            "Leave_Start": routes["leave_start"].apply(format_time).values,
            "Arrive_Mid": routes["arr_via1"].apply(format_time).values,
            "Leg1_Hrs": routes["t1_hrs"].round(1).values,
            "Layover1_Hrs": routes["lay1"].round(1).values,
            "Train_2_No": routes["train2"].values,
            "Train_2_Name": routes["train2_name"].values,
            "Train_2_Type": [classify_train_type(n) for n in routes["train2_name"]],
            "Leave_Mid": routes["leave_via1"].apply(format_time).values,
            "Arrive_Via2": routes["arr_via2"].apply(format_time).values,
            "Leg2_Hrs": routes["t2_hrs"].round(1).values,
            "Layover2_Hrs": routes["lay2"].round(1).values,
            "Train_3_No": routes["train3"].values,
            "Train_3_Name": routes["train3_name"].values,
            "Train_3_Type": [classify_train_type(n) for n in routes["train3_name"]],
            "Leave_Via2": routes["leave_via2"].apply(format_time).values,
            "Arrive_End": routes["arrive_end"].apply(format_time).values,
            "Leg3_Hrs": routes["t3_hrs"].round(1).values,
            "Layover_Hrs": (routes["lay1"] + routes["lay2"]).round(1).values,
            "Total_Hrs": routes["total_hrs"].round(1).values,
            "Comfort": routes["lay1"].apply(comfort_label).values,
            "Train_1_Running_Days": [running_days_text(t) for t in routes["train1"]],
            "Train_2_Running_Days": [running_days_text(t) for t in routes["train2"]],
            "Train_3_Running_Days": [running_days_text(t) for t in routes["train3"]],
            "Train2_Day_Offset": routes["train2_day_offset"].values,
            "Train3_Day_Offset": routes["train3_day_offset"].values,
            "Board_Date": [
                d.isoformat() if isinstance(d, datetime.date) else ""
                for d in routes["boarding_date"]
            ],
            "Board_On": [
                d.strftime("%a %d %b %Y") if isinstance(d, datetime.date) else ""
                for d in routes["boarding_date"]
            ],
        }
    )

    # Halt warnings at both vias
    warns = []
    for _, row in out.iterrows():
        h1 = _halt_minutes_at(row["Train_2_No"], row["Via1_Code"])
        h2 = _halt_minutes_at(row["Train_3_No"], row["Via2_Code"])
        w1 = change_warning_text(None, h1, row["Layover1_Hrs"])
        w2 = change_warning_text(None, h2, row["Layover2_Hrs"])
        parts = [p for p in (w1, w2) if p]
        if parts:
            warns.append(" | ".join(parts))
        else:
            warns.append("")
    out["Change_Warning"] = warns
    out["Overnight_Layover"] = [
        "Yes" if (float(a) > 6 or float(b) > 6) else "No"
        for a, b in zip(out["Layover1_Hrs"], out["Layover2_Hrs"])
    ]

    if search_date is None:
        out = out.drop(columns=["Board_On", "Board_Date"], errors="ignore")

    if out["Start_From"].nunique() <= 1 and out["End_At"].nunique() <= 1:
        out = out.drop(columns=["Start_From", "End_At"], errors="ignore")

    sort_col = {"fastest": "Total_Hrs", "layover": "Layover_Hrs", "departure": "Leave_Start"}.get(
        sort_by, "Total_Hrs"
    )
    out = out.sort_values(sort_col).reset_index(drop=True)
    if max_results:
        out = out.head(int(max_results))
    return out


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
    train_types=None,
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
    if train_types and not merged.empty:
        merged = merged[merged["train_name"].apply(lambda n: _train_type_ok(n, train_types))]

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
    merged["Train_Type"] = merged["train_name"].apply(classify_train_type)
    merged["Stops_Between"] = (merged["end_seq"] - merged["start_seq"] - 1).astype(int)

    out_cols = [
        "train_number",
        "train_name",
        "Train_Type",
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
