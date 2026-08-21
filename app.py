import datetime
from typing import Optional

import pandas as pd
import streamlit as st

try:
    from engine import (
        find_connections_pro,
        find_two_change_connections,
        find_direct_trains,
        get_possible_via_stations,
        get_trains_at_station,
        get_top_via_hubs,
        station_list,
        search_trains,
        get_train_schedule,
        get_train_summary,
        format_duration,
        irctc_train_url,
        get_nearby_station_codes,
        nearby_station_labels,
        station_label,
        get_data_stats,
        TRAIN_TYPE_OPTIONS,
        WEEKDAYS,
    )
    from live_availability import (
        fetch_connection_availability,
        fetch_route_availability,
        pick_train,
        status_tone,
        filter_classes,
        score_connection_seats,
        TRAVEL_CLASSES,
    )
except FileNotFoundError as e:
    st.set_page_config(page_title="Train Connector", layout="wide")
    st.error(
        f"Couldn't load one of the data files: **{e}**.\n\n"
        "Make sure `stations.csv`, `train_schedule_scrapped.csv` and "
        "`running_days_scrapped.csv` are all present in the app folder."
    )
    st.stop()


# =====================================================================================
# PAGE + THEME
# =====================================================================================
st.set_page_config(
    page_title="Train Connector",
    layout="wide",
    page_icon="🚆",
    initial_sidebar_state="collapsed",
)

if "theme" not in st.session_state:
    st.session_state.theme = "dark"
if "swap_tick" not in st.session_state:
    st.session_state.swap_tick = 0
if "conn_results" not in st.session_state:
    st.session_state.conn_results = None
if "conn_meta" not in st.session_state:
    st.session_state.conn_meta = {}
if "seat_cache" not in st.session_state:
    st.session_state.seat_cache = {}
if "direct_results" not in st.session_state:
    st.session_state.direct_results = None
if "direct_meta" not in st.session_state:
    st.session_state.direct_meta = {}
if "direct_seat_cache" not in st.session_state:
    st.session_state.direct_seat_cache = {}
if "show_direct_from_conn" not in st.session_state:
    st.session_state.show_direct_from_conn = False
if "conn_directs" not in st.session_state:
    st.session_state.conn_directs = None
if "qp_bootstrapped" not in st.session_state:
    st.session_state.qp_bootstrapped = False
if "saved_searches" not in st.session_state:
    st.session_state.saved_searches = []
if "two_change_results" not in st.session_state:
    st.session_state.two_change_results = None


def remember_search(meta: dict):
    """Keep the last 5 connection searches for one-click replay."""
    entry = {
        "start_station": meta.get("start_station"),
        "end_station": meta.get("end_station"),
        "s_code": meta.get("s_code"),
        "e_code": meta.get("e_code"),
        "search_date": meta.get("search_date"),
        "flexible": bool(meta.get("flexible")),
        "include_nearby": bool(meta.get("include_nearby")),
        "pref_classes": list(meta.get("pref_classes") or []),
        "train_types": list(meta.get("train_types") or []),
        "quota": meta.get("quota") or "GN",
        "allow_two_change": bool(meta.get("allow_two_change")),
        "label": (
            f"{meta.get('s_code', '?')} → {meta.get('e_code', '?')}"
            + (f" · {meta['search_date'].isoformat()}" if meta.get("search_date") else "")
        ),
    }
    existing = [
        s
        for s in st.session_state.saved_searches
        if not (
            s.get("s_code") == entry["s_code"]
            and s.get("e_code") == entry["e_code"]
            and s.get("search_date") == entry["search_date"]
        )
    ]
    st.session_state.saved_searches = ([entry] + existing)[:5]


def _label_for_code(code: str) -> str:
    """Find 'NAME (CODE)' in station_list, else build a label."""
    code = (code or "").strip().upper()
    if not code:
        return ""
    suffix = f"({code})"
    for label in station_list:
        if label.endswith(suffix):
            return label
    return station_label(code)


def _qp_get(name: str, default: str = "") -> str:
    try:
        val = st.query_params.get(name, default)
    except Exception:
        return default
    if isinstance(val, list):
        return str(val[0]) if val else default
    return str(val) if val is not None else default


def bootstrap_from_query_params():
    """Prefill connection search widgets once from ?from=&to=&date=…"""
    if st.session_state.qp_bootstrapped:
        return
    st.session_state.qp_bootstrapped = True

    from_code = _qp_get("from").upper()
    to_code = _qp_get("to").upper()
    if from_code:
        st.session_state[f"start_{st.session_state.swap_tick}"] = _label_for_code(from_code)
    if to_code:
        st.session_state[f"end_{st.session_state.swap_tick}"] = _label_for_code(to_code)

    date_s = _qp_get("date")
    if date_s:
        try:
            st.session_state["conn_travel_date"] = datetime.date.fromisoformat(date_s)
            st.session_state["conn_use_date"] = True
        except ValueError:
            pass

    flex = _qp_get("flex")
    if flex in ("1", "true", "yes"):
        st.session_state["conn_flex_on"] = True
    elif flex in ("0", "false", "no"):
        st.session_state["conn_flex_on"] = False

    nearby = _qp_get("nearby")
    if nearby in ("1", "true", "yes"):
        st.session_state["conn_nearby"] = True
    elif nearby in ("0", "false", "no"):
        st.session_state["conn_nearby"] = False

    classes = _qp_get("class")
    if classes:
        picked = [c.strip().upper() for c in classes.split(",") if c.strip()]
        st.session_state["conn_pref_classes"] = [c for c in picked if c in TRAVEL_CLASSES]

    quota = _qp_get("quota").upper()
    if quota in ("GN", "TQ", "PT", "LD", "SS"):
        st.session_state["conn_quota"] = quota

    theme = _qp_get("theme").lower()
    if theme in ("dark", "light"):
        st.session_state.theme = theme


def update_search_query_params(meta: dict):
    """Write current connection search into the browser URL for sharing."""
    params = {}
    if meta.get("s_code"):
        params["from"] = meta["s_code"]
    if meta.get("e_code"):
        params["to"] = meta["e_code"]
    if meta.get("search_date"):
        params["date"] = meta["search_date"].isoformat()
    if meta.get("flexible"):
        params["flex"] = "1"
    if meta.get("include_nearby"):
        params["nearby"] = "1"
    prefs = meta.get("pref_classes") or []
    if prefs:
        params["class"] = ",".join(prefs)
    if meta.get("quota") and meta["quota"] != "GN":
        params["quota"] = meta["quota"]
    params["theme"] = st.session_state.theme
    try:
        st.query_params.clear()
        st.query_params.update(params)
    except Exception:
        pass


def build_share_url(meta: dict) -> str:
    """Relative share link with query string (works on Streamlit Cloud / local)."""
    parts = []
    if meta.get("s_code"):
        parts.append(f"from={meta['s_code']}")
    if meta.get("e_code"):
        parts.append(f"to={meta['e_code']}")
    if meta.get("search_date"):
        parts.append(f"date={meta['search_date'].isoformat()}")
    if meta.get("flexible"):
        parts.append("flex=1")
    if meta.get("include_nearby"):
        parts.append("nearby=1")
    prefs = meta.get("pref_classes") or []
    if prefs:
        parts.append("class=" + ",".join(prefs))
    if meta.get("quota") and meta["quota"] != "GN":
        parts.append(f"quota={meta['quota']}")
    return ("?" + "&".join(parts)) if parts else ""


def connection_cache_key(row, start_code, end_code, search_date, quota) -> str:
    board = str(row.get("Board_Date") or search_date or "")
    return (
        f"{start_code}|{row.get('Via_Code')}|{end_code}|"
        f"{row.get('Train_1_No')}|{row.get('Train_2_No')}|"
        f"{board}|{quota}|{row.get('Train2_Day_Offset', 0)}"
    )


def format_connection_itinerary(
    results: pd.DataFrame,
    start_label: str,
    end_label: str,
    limit: int = 5,
) -> str:
    lines = [
        "Train Connector itinerary",
        f"{start_label} → {end_label}",
        "",
    ]
    for i, (_, row) in enumerate(results.head(limit).iterrows(), start=1):
        board = row.get("Board_On") or "Any day"
        start_from = row.get("Start_From") or start_label
        end_at = row.get("End_At") or end_label
        lines.append(f"Option {i} · via {row['Via_Station']} ({row['Via_Code']})")
        lines.append(f"  Board: {board}")
        lines.append(
            f"  Train 1: {row['Train_1_No']} {row['Train_1_Name']} · "
            f"{start_from} {row['Leave_Start']} → {row['Arrive_Mid']}"
        )
        lines.append(
            f"  Wait {format_duration(row['Layover_Hrs'])} ({row.get('Comfort', '')})"
        )
        lines.append(
            f"  Train 2: {row['Train_2_No']} {row['Train_2_Name']} · "
            f"{row['Leave_Mid']} → {end_at} {row['Arrive_End']}"
        )
        lines.append(f"  Total: {format_duration(row['Total_Hrs'])}")
        if row.get("Seat_Score") is not None and str(row.get("Seat_Score")) not in ("", "nan"):
            fare = row.get("Est_Fare")
            fare_bit = f" · ~₹{int(fare)}" if pd.notna(fare) else ""
            lines.append(f"  Seats: score {row['Seat_Score']}{fare_bit}")
        lines.append("")
    lines.append("Confirm on IRCTC before booking.")
    return "\n".join(lines)


def apply_seat_scores_to_results(
    results: pd.DataFrame,
    start_code: str,
    end_code: str,
    search_date,
    quota: str,
    pref_classes: list,
) -> pd.DataFrame:
    """Attach Seat_Score / Est_Fare from seat_cache where available."""
    if results is None or results.empty:
        return results
    out = results.copy()
    scores, fares = [], []
    for _, row in out.iterrows():
        key = connection_cache_key(row, start_code, end_code, search_date, quota)
        seat = st.session_state.seat_cache.get(key)
        if not seat:
            scores.append(None)
            fares.append(None)
            continue
        scored = score_connection_seats(seat, pref_classes or None)
        scores.append(scored["seat_score"] if scored["seat_score"] >= 0 else None)
        fares.append(scored["est_fare"])
    out["Seat_Score"] = scores
    out["Est_Fare"] = fares
    return out


def sort_results_df(results: pd.DataFrame, sort_by: str) -> pd.DataFrame:
    if results is None or results.empty:
        return results
    out = results.copy()
    if sort_by == "seats":
        if "Seat_Score" in out.columns and out["Seat_Score"].notna().any():
            out = out.sort_values(
                ["Seat_Score", "Total_Hrs"],
                ascending=[False, True],
                na_position="last",
            )
        else:
            out = out.sort_values("Total_Hrs")
    elif sort_by == "fare":
        if "Est_Fare" in out.columns and out["Est_Fare"].notna().any():
            out = out.sort_values(
                ["Est_Fare", "Total_Hrs"],
                ascending=[True, True],
                na_position="last",
            )
        else:
            out = out.sort_values("Total_Hrs")
    elif sort_by == "layover":
        out = out.sort_values("Layover_Hrs")
    elif sort_by == "departure":
        out = out.sort_values("Leave_Start")
    else:
        out = out.sort_values("Total_Hrs")
    return out.reset_index(drop=True)


def extract_code(station_string):
    """'NEW DELHI (NDLS)' -> 'NDLS'"""
    if station_string:
        return station_string.split("(")[-1].replace(")", "").strip()
    return None


def comfort_color(label: str, dark: bool) -> str:
    palette = {
        "Tight": "#f59e0b" if dark else "#b45309",
        "Comfortable": "#34d399" if dark else "#15803d",
        "Relaxed": "#60a5fa" if dark else "#1d4ed8",
        "Long wait": "#a78bfa" if dark else "#6d28d9",
        "Overnight+": "#f87171" if dark else "#b91c1c",
    }
    return palette.get(label, "#94a3b8" if dark else "#475569")


def apply_theme_css(theme: str):
    dark = theme == "dark"
    if dark:
        bg = "#0b1220"
        bg2 = "#121a2b"
        panel = "#162033"
        panel2 = "#1c2940"
        text = "#f8fafc"
        muted = "#cbd5e1"
        border = "#334155"
        primary = "#3b82f6"
        primary_h = "#2563eb"
        accent = "#2dd4bf"
        input_bg = "#1e293b"
        btn_bg = "#1e293b"
        btn_text = "#f8fafc"
        hero_grad = "linear-gradient(135deg, #0b1220 0%, #132447 48%, #0f3d3a 100%)"
        shadow = "0 12px 40px rgba(0,0,0,0.35)"
        alert_bg = "#1c2940"
    else:
        bg = "#f4f7fb"
        bg2 = "#ffffff"
        panel = "#ffffff"
        panel2 = "#eef3f9"
        text = "#0f172a"
        muted = "#475569"
        border = "#d5deea"
        primary = "#1d4ed8"
        primary_h = "#1e40af"
        accent = "#0f766e"
        input_bg = "#ffffff"
        btn_bg = "#ffffff"
        btn_text = "#0f172a"
        hero_grad = "linear-gradient(135deg, #e8f1ff 0%, #f7fbff 45%, #e6f7f4 100%)"
        shadow = "0 10px 30px rgba(15, 23, 42, 0.08)"
        alert_bg = "#eef3f9"

    st.markdown(
        f"""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Fraunces:opsz,wght@9..144,600;9..144,700&display=swap');

        /* Streamlit theme tokens — widgets read these */
        :root, .stApp, [data-testid="stAppViewContainer"] {{
            --primary-color: {primary} !important;
            --background-color: {bg} !important;
            --secondary-background-color: {panel2} !important;
            --text-color: {text} !important;
            color-scheme: {"dark" if dark else "light"};
        }}

        html, body, [class*="css"] {{
            font-family: 'DM Sans', sans-serif !important;
            color: {text} !important;
        }}

        .stApp, .stApp > header, [data-testid="stAppViewContainer"],
        [data-testid="stMain"], section.main, .main {{
            background-color: {bg} !important;
            color: {text} !important;
        }}

        [data-testid="stHeader"] {{
            background: {bg} !important;
        }}

        .block-container {{
            padding-top: 1.2rem;
            padding-bottom: 3rem;
            max-width: 1180px;
        }}

        /* Force readable text on Streamlit content — but NOT inside inputs */
        .stMarkdown, .stMarkdown p, .stMarkdown li,
        .stMarkdown h1, .stMarkdown h2, .stMarkdown h3, .stMarkdown h4,
        .stMarkdown h5, .stMarkdown h6, .stCaption, [data-testid="stCaption"],
        .stSelectbox label, .stRadio label, .stCheckbox label,
        .stSlider label, .stNumberInput label, .stTextInput label,
        .stDateInput label, .stTimeInput label, .stToggle label,
        h1, h2, h3, h4, h5, h6 {{
            color: {text} !important;
        }}

        .stMarkdown strong, .stMarkdown b {{
            color: {text} !important;
        }}

        /* Hero */
        .tc-hero {{
            background: {hero_grad};
            border: 1px solid {border};
            border-radius: 22px;
            padding: 1.6rem 1.8rem 1.4rem;
            margin-bottom: 1.1rem;
            box-shadow: {shadow};
            position: relative;
            overflow: hidden;
        }}
        .tc-hero::after {{
            content: "";
            position: absolute;
            right: -40px;
            top: -40px;
            width: 180px;
            height: 180px;
            border-radius: 50%;
            background: radial-gradient(circle, {accent}33, transparent 70%);
            pointer-events: none;
        }}
        .tc-brand {{
            font-family: 'Fraunces', Georgia, serif;
            font-size: 2.05rem;
            font-weight: 700;
            letter-spacing: -0.02em;
            margin: 0;
            color: {text} !important;
            line-height: 1.15;
        }}
        .tc-sub {{
            margin: 0.45rem 0 0;
            color: {muted} !important;
            font-size: 1.02rem;
            max-width: 52ch;
        }}
        .tc-chip-row {{
            display: flex;
            flex-wrap: wrap;
            gap: 0.45rem;
            margin-top: 0.95rem;
        }}
        .tc-chip {{
            font-size: 0.78rem;
            font-weight: 600;
            letter-spacing: 0.02em;
            padding: 0.28rem 0.65rem;
            border-radius: 999px;
            border: 1px solid {border};
            background: {panel2};
            color: {muted} !important;
        }}

        /* Date strip */
        .tc-date-panel {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 18px;
            padding: 1rem 1.15rem 0.35rem;
            margin: 0.35rem 0 1rem;
            box-shadow: {shadow};
        }}
        .tc-date-label {{
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.08em;
            color: {accent} !important;
            margin-bottom: 0.15rem;
        }}
        .tc-date-hint {{
            color: {muted} !important;
            font-size: 0.88rem;
            margin-top: -0.35rem;
            margin-bottom: 0.55rem;
        }}

        .tc-panel {{
            background: {panel};
            border: 1px solid {border};
            border-radius: 16px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.85rem;
            color: {text} !important;
        }}
        .tc-route-box {{
            border-left: 4px solid var(--route-color, {primary});
            background: {panel2};
            border-radius: 0 12px 12px 0;
            padding: 0.9rem 1rem;
            margin: 0.55rem 0 0.75rem;
            color: {text} !important;
        }}
        .tc-muted {{ color: {muted} !important; font-size: 0.85rem; }}
        .tc-seat-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
            gap: 0.5rem;
            margin-top: 0.55rem;
        }}
        .tc-seat-pill {{
            border-radius: 10px;
            border: 1px solid {border};
            background: {bg2};
            padding: 0.55rem 0.65rem;
            font-size: 0.84rem;
            color: {text} !important;
        }}
        .tc-seat-pill .cls {{
            font-weight: 700;
            font-size: 0.78rem;
            letter-spacing: 0.04em;
            color: {muted} !important;
            margin-bottom: 0.15rem;
        }}
        .tc-seat-pill.good {{ border-color: #22c55e88; background: {"#10241a" if dark else "#f0fdf4"}; }}
        .tc-seat-pill.warn {{ border-color: #f59e0b88; background: {"#2a2110" if dark else "#fffbeb"}; }}
        .tc-seat-pill.wait {{ border-color: #38bdf888; background: {"#0f2230" if dark else "#f0f9ff"}; }}
        .tc-seat-pill.bad {{ border-color: #ef444488; background: {"#2a1414" if dark else "#fef2f2"}; }}

        div[data-testid="stVerticalBlockBorderWrapper"] {{
            background: {panel} !important;
            border-color: {border} !important;
            color: {text} !important;
        }}

        .stSelectbox label, .stRadio label, .stCheckbox label,
        .stSlider label, .stNumberInput label, .stTextInput label,
        .stDateInput label, .stTimeInput label, .stToggle label {{
            color: {text} !important;
            font-weight: 600 !important;
        }}

        /* Inputs — force visible text in dark + light */
        .stSelectbox div[data-baseweb="select"] > div,
        div[data-baseweb="select"] > div,
        div[data-baseweb="select"] > div > div,
        div[data-baseweb="select"] *,
        div[data-baseweb="select"] span,
        div[data-baseweb="select"] input,
        div[data-baseweb="input"] input,
        .stTextInput input, .stNumberInput input,
        div[data-testid="stDateInput"] input,
        div[data-testid="stTextInput"] input,
        textarea {{
            background-color: {input_bg} !important;
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
            caret-color: {text} !important;
            border-color: {border} !important;
        }}

        div[data-baseweb="select"] svg {{
            fill: {text} !important;
            color: {text} !important;
        }}

        div[data-baseweb="select"] [data-testid="stMarkdownContainer"],
        div[data-baseweb="select"] [data-testid="stMarkdownContainer"] *,
        div[data-baseweb="select"] div[aria-selected="true"],
        div[data-baseweb="select"] input {{
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
        }}

        [data-testid="stWidgetLabel"],
        [data-testid="stWidgetLabel"] *,
        [data-testid="stMarkdownContainer"] p,
        [data-testid="stMarkdownContainer"] span {{
            color: {text} !important;
        }}

        /* Placeholder */
        div[data-baseweb="select"] input::placeholder,
        .stTextInput input::placeholder,
        div[data-testid="stDateInput"] input::placeholder {{
            color: {muted} !important;
            -webkit-text-fill-color: {muted} !important;
            opacity: 0.85 !important;
        }}

        div[data-baseweb="popover"] ul,
        div[data-baseweb="menu"],
        ul[role="listbox"],
        li[role="option"] {{
            background-color: {panel} !important;
            color: {text} !important;
        }}
        li[role="option"] span,
        li[role="option"] div {{
            color: {text} !important;
        }}
        li[role="option"]:hover,
        li[aria-selected="true"] {{
            background-color: {panel2} !important;
        }}

        div[data-testid="stDateInput"] input {{
            font-size: 1.2rem !important;
            font-weight: 700 !important;
            padding: 0.85rem 0.9rem !important;
            border-radius: 12px !important;
            border: 2px solid {primary}55 !important;
            min-height: 3.4rem !important;
            background: {input_bg} !important;
            color: {text} !important;
            -webkit-text-fill-color: {text} !important;
        }}

        /* Radio / checkbox text */
        .stRadio label p, .stCheckbox label p, .stToggle label p {{
            color: {text} !important;
        }}

        .stButton > button {{
            border-radius: 12px !important;
            font-weight: 650 !important;
            border: 1px solid {border} !important;
            background: {btn_bg} !important;
            color: {btn_text} !important;
        }}
        .stButton > button[kind="primary"] {{
            background: {primary} !important;
            color: #ffffff !important;
            border: none !important;
            box-shadow: 0 8px 20px {primary}44;
        }}
        .stButton > button[kind="primary"]:hover {{
            background: {primary_h} !important;
            color: #ffffff !important;
        }}

        .stTabs [data-baseweb="tab-list"] {{
            gap: 0.35rem;
            background: {panel2} !important;
            padding: 0.35rem;
            border-radius: 14px;
            border: 1px solid {border};
        }}
        .stTabs [data-baseweb="tab"] {{
            border-radius: 10px;
            color: {muted} !important;
            font-weight: 600;
            background: transparent !important;
        }}
        .stTabs [aria-selected="true"] {{
            background: {panel} !important;
            color: {text} !important;
        }}

        div[data-testid="stMetric"] {{
            background: {panel2} !important;
            border: 1px solid {border};
            border-radius: 14px;
            padding: 0.65rem 0.85rem;
        }}
        div[data-testid="stMetricValue"],
        div[data-testid="stMetricValue"] * {{
            color: {text} !important;
        }}
        div[data-testid="stMetricLabel"],
        div[data-testid="stMetricLabel"] * {{
            color: {muted} !important;
        }}

        .stExpander, [data-testid="stExpander"],
        [data-testid="stExpander"] details,
        [data-testid="stExpander"] summary {{
            background: {panel} !important;
            border-color: {border} !important;
            color: {text} !important;
            border-radius: 14px !important;
        }}
        [data-testid="stExpander"] summary span,
        [data-testid="stExpander"] p,
        [data-testid="stExpander"] div {{
            color: {text} !important;
        }}

        div[data-testid="stAlert"] {{
            background: {alert_bg} !important;
            color: {text} !important;
        }}
        div[data-testid="stAlert"] * {{
            color: {text} !important;
        }}

        [data-testid="stDataFrame"],
        [data-testid="stDataFrame"] * {{
            color: {text};
        }}

        hr {{
            border-color: {border} !important;
        }}

        .tc-footer {{
            text-align: center;
            color: {muted} !important;
            padding-top: 1.25rem;
            font-size: 0.9rem;
        }}
        .tc-footer b {{
            color: {text} !important;
        }}
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_running_day_badges(bits: str, dark: bool):
    on = "#0f766e" if not dark else "#14b8a6"
    off = "#334155" if dark else "#cbd5e1"
    badge_cols = st.columns(7)
    if bits:
        for i, day in enumerate(WEEKDAYS):
            runs = bits[i] == "1"
            badge_cols[i].markdown(
                f"<div style='text-align:center; padding:7px; border-radius:8px; "
                f"background:{on if runs else off}; color:white; font-size:13px; font-weight:600;'>"
                f"{day}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No running-day data on file — assumed to run daily.")


def _parse_board_date(row) -> Optional[datetime.date]:
    iso = str(row.get("Board_Date") or "").strip()
    if iso:
        try:
            return datetime.date.fromisoformat(iso)
        except ValueError:
            pass
    return None


def _now_india() -> datetime.datetime:
    """Current time in India (IST, UTC+5:30)."""
    return datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)


def _parse_hhmm(time_str) -> Optional[datetime.time]:
    s = str(time_str or "").strip()
    if not s:
        return None
    parts = s.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return datetime.time(h % 24, m % 60)
    except (ValueError, TypeError):
        return None


def departure_booking_status(
    board_date: Optional[datetime.date],
    dep_time_str: str = "",
) -> dict:
    """
    Compare boarding date + departure time with current IST time.
    Returns status: bookable | departed | no_booking | unknown
    """
    now = _now_india()
    today = now.date()
    dep_t = _parse_hhmm(dep_time_str)

    if board_date is None:
        return {
            "status": "unknown",
            "departed": False,
            "label": "",
            "scraped_date_label": "",
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    scraped_label = board_date.strftime("%a %d %b %Y")
    dep_label = dep_t.strftime("%H:%M") if dep_t else (str(dep_time_str) or "—")

    if board_date < today:
        return {
            "status": "departed",
            "departed": True,
            "label": f"No more booking — train already left on {scraped_label} (dep {dep_label}).",
            "scraped_date_label": scraped_label,
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    if board_date == today and dep_t is not None:
        dep_dt = datetime.datetime.combine(board_date, dep_t)
        if now >= dep_dt:
            return {
                "status": "departed",
                "departed": True,
                "label": (
                    f"No more booking — train departed at {dep_label} "
                    f"(now {now.strftime('%I:%M %p')} IST)."
                ),
                "scraped_date_label": scraped_label,
                "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
            }

    if board_date == today:
        return {
            "status": "bookable",
            "departed": False,
            "label": f"Today · departs {dep_label}",
            "scraped_date_label": scraped_label,
            "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
        }

    return {
        "status": "bookable",
        "departed": False,
        "label": f"Upcoming · {scraped_label} · dep {dep_label}",
        "scraped_date_label": scraped_label,
        "now_label": now.strftime("%d %b %Y, %I:%M %p IST"),
    }


def _enrich_seat_result(
    seat: dict,
    board_date: Optional[datetime.date],
    dep_time_str: str = "",
) -> dict:
    """Attach scraped-date + departed info to a seat result dict."""
    out = dict(seat or {})
    status = departure_booking_status(board_date, dep_time_str)
    out["scraped_date"] = board_date.isoformat() if board_date else out.get("date", "")
    out["scraped_date_label"] = status["scraped_date_label"] or out.get("date", "")
    out["departed"] = status["departed"]
    out["booking_status"] = status["status"]
    out["booking_label"] = status["label"]
    out["checked_at"] = status["now_label"]
    out["dep_time"] = str(dep_time_str or "")
    # Also detect API "TRAIN DEPARTED" style statuses
    for row in out.get("classes") or []:
        st_txt = str(row.get("status") or "").upper()
        if "DEPART" in st_txt:
            out["departed"] = True
            if not out.get("booking_label") or out.get("booking_status") != "departed":
                out["booking_label"] = "No more booking — train departed (from live data)."
            out["booking_status"] = "departed"
            break
    return out


def render_seat_block(leg: dict, title: str, pref_classes: Optional[list] = None):
    scraped = leg.get("scraped_date_label") or leg.get("date") or "—"
    st.markdown(f"**{title}** · `{leg.get('train_number', '')}` {leg.get('train_name', '')}")
    st.caption(
        f"{leg.get('from', '?')} → {leg.get('to', '?')}  ·  "
        f"**Scraped for:** {scraped}"
        + (f"  ·  Dep {leg.get('dep_time')}" if leg.get("dep_time") else "")
        + (f"  ·  Checked at {leg.get('checked_at')}" if leg.get("checked_at") else "")
    )

    if leg.get("departed") or leg.get("booking_status") == "departed":
        st.error(leg.get("booking_label") or "No more booking — train has departed.")
    elif leg.get("booking_label"):
        st.info(leg.get("booking_label"))

    if not leg.get("ok"):
        st.warning(leg.get("error") or "Could not get seat info for this train.")
        return
    if not leg.get("found"):
        if leg.get("departed"):
            st.caption("Live list may hide departed trains — booking is closed for this departure.")
        else:
            st.info("This train was not found in live seat results for this date. Please check on IRCTC.")
        return
    classes = filter_classes(leg.get("classes") or [], pref_classes)
    if not classes:
        st.caption("No seat class info returned — please check on IRCTC.")
        return
    if pref_classes:
        st.caption("Showing preferred class(es): " + ", ".join(pref_classes))
    pills = []
    for row in classes:
        tone = status_tone(row.get("status", ""))
        pills.append(
            f"<div class='tc-seat-pill {tone}'>"
            f"<div class='cls'>{row.get('class') or '?'}</div>"
            f"<div>{row.get('status') or '—'}</div>"
        )
        extra = []
        if row.get("prediction"):
            extra.append(row["prediction"] + (f" ({row['chance']}%)" if row.get("chance") else ""))
        if row.get("fare"):
            extra.append(f"₹{row['fare']}")
        if extra:
            pills[-1] += (
                f"<div class='tc-muted' style='margin-top:2px;font-size:0.75rem;'>"
                f"{' · '.join(extra)}</div>"
            )
        pills[-1] += "</div>"

    st.markdown(f"<div class='tc-seat-grid'>{''.join(pills)}</div>", unsafe_allow_html=True)

def render_direct_train_cards(
    directs: pd.DataFrame,
    start_code: str,
    end_code: str,
    start_label: str,
    end_label: str,
    quota: str = "GN",
    key_prefix: str = "direct",
    pref_classes: Optional[list] = None,
):
    """Show direct trains as cards with optional live seat check (no CSV / IRCTC links)."""
    st.subheader("Direct trains")
    st.caption(f"{start_label} → {end_label}")

    for idx, (_, row) in enumerate(directs.iterrows()):
        board = row.get("Board_On") or "Any day"
        title = (
            f"{row['Train_No']} · {row['Train_Name']}  ·  "
            f"{row['Departure']} → {row['Arrival']}  ·  "
            f"{format_duration(row['Duration_Hrs'])}"
        )
        with st.expander(title, expanded=(idx < 3)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Departure", row["Departure"])
            c2.metric("Arrival", row["Arrival"])
            c3.metric("Duration", format_duration(row["Duration_Hrs"]))
            c4.metric("Stops between", int(row.get("Stops_Between", 0) or 0))

            also_ok = str(row.get("Also_OK_On") or "").strip()
            also_html = (
                f"<div class='tc-muted' style='margin-top:4px;'>Also OK on: {also_ok}</div>"
                if also_ok
                else ""
            )
            start_from = row.get("Start_From") or start_label
            end_at = row.get("End_At") or end_label
            st.markdown(
                f"""
                <div class="tc-route-box">
                  <div><b>{row['Train_No']}</b> — {row['Train_Name']}</div>
                  <div class="tc-muted" style="margin-top:6px;">
                    {start_from} → {end_at}
                  </div>
                  <div class="tc-muted" style="margin-top:6px;">
                    Board on: <b>{board}</b>
                    &nbsp;·&nbsp; Runs (from origin): {row.get('Running_Days', '')}
                    &nbsp;·&nbsp; Leaves origin on: {row.get('Leaves_Origin_On', '—')}
                  </div>
                  <div class="tc-muted" style="margin-top:4px;">
                    Journey day at start: {row.get('Journey_Day_At_Start', '—')}
                    &nbsp;·&nbsp; Journey day at end: {row.get('Journey_Day_At_End', '—')}
                  </div>
                  {also_html}
                </div>
                """,
                unsafe_allow_html=True,
            )

            board_date = _parse_board_date(row)
            dep_time = str(row.get("Departure") or "")
            pre_status = departure_booking_status(board_date, dep_time)
            if pre_status["departed"]:
                st.error(pre_status["label"])
            elif board_date:
                st.caption(f"Seat check date: **{pre_status['scraped_date_label']}** · {pre_status['label']}")

            from_code = extract_code(str(row.get("Start_From") or "")) or start_code
            to_code = extract_code(str(row.get("End_At") or "")) or end_code
            cache_key = f"{key_prefix}|{from_code}|{to_code}|{row['Train_No']}|{board_date}|{quota}"

            if st.button(
                "Check seats",
                key=f"{key_prefix}_seat_{idx}_{row['Train_No']}",
                use_container_width=True,
                disabled=board_date is None,
                help="Gets live seat availability for this train on the boarding date.",
            ):
                with st.spinner(f"Getting seats for train {row['Train_No']}…"):
                    route = fetch_route_availability(from_code, to_code, board_date, quota)
                    seat = pick_train(route, str(row["Train_No"]))
                    st.session_state.direct_seat_cache[cache_key] = _enrich_seat_result(
                        seat, board_date, dep_time
                    )
                st.rerun()

            if board_date is None:
                st.caption("Choose a travel date to check seats.")

            seat = st.session_state.direct_seat_cache.get(cache_key)
            if seat:
                st.markdown("##### Seat availability")
                render_seat_block(seat, "Train", pref_classes=pref_classes)
                st.caption("Seat info from ConfirmTkt. Confirm on IRCTC before booking.")


def render_connection_cards(
    results: pd.DataFrame,
    start_label: str,
    end_label: str,
    start_code: str,
    end_code: str,
    search_date: Optional[datetime.date],
    quota: str,
    dark: bool,
    limit: int = 12,
    pref_classes: Optional[list] = None,
):
    st.subheader("Route options")
    st.caption(
        f"Showing top {min(limit, len(results))} of {len(results)}. "
        "Use **Check seats (both trains)** to see seats for Train 1 and Train 2 at the same time."
    )

    for i, row in results.head(limit).iterrows():
        comfort = row.get("Comfort", "")
        overnight = row.get("Overnight_Layover", "No") == "Yes"
        seat_bit = ""
        if row.get("Seat_Score") is not None and pd.notna(row.get("Seat_Score")):
            seat_bit = f"  ·  seats {row['Seat_Score']:.0f}"
            if row.get("Est_Fare") is not None and pd.notna(row.get("Est_Fare")):
                seat_bit += f" · ~₹{int(row['Est_Fare'])}"
        title = (
            f"#{i + 1}  ·  via {row['Via_Station']} ({row['Via_Code']})  ·  "
            f"{format_duration(row['Total_Hrs'])} total  ·  "
            f"wait {format_duration(row['Layover_Hrs'])}{seat_bit}"
        )
        with st.expander(title, expanded=(i < 2)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", format_duration(row["Total_Hrs"]))
            c2.metric("Wait", format_duration(row["Layover_Hrs"]))
            c3.metric("Comfort", comfort)
            c4.metric("Night wait", "Yes" if overnight else "No")

            color = comfort_color(str(comfort), dark)
            start_from = row.get("Start_From") or start_label
            end_at = row.get("End_At") or end_label
            board_on = row.get("Board_On") or ""
            board_html = (
                f"<div class='tc-muted' style='margin-bottom:6px;'>Board on: <b>{board_on}</b></div>"
                if board_on
                else ""
            )
            also_ok = str(row.get("Also_OK_On") or "").strip()
            also_html = (
                f"<div class='tc-muted' style='margin-top:4px;font-size:12px;'>Also OK on: {also_ok}</div>"
                if also_ok
                else ""
            )
            st.markdown(
                f"""
                <div class="tc-route-box" style="--route-color:{color};">
                  {board_html}
                  <div class="tc-muted" style="margin-bottom:6px;">
                    {start_from} → <b>{row['Via_Station']}</b> → {end_at}
                  </div>
                  <div style="display:flex; flex-wrap:wrap; gap:18px; font-size:14px;">
                    <div>
                      <div class="tc-muted" style="font-size:11px;">TRAIN 1 · {row['Train_1_No']}</div>
                      <div><b>{row['Train_1_Name']}</b></div>
                      <div>{row['Leave_Start']} → {row['Arrive_Mid']}
                           <span class="tc-muted">({format_duration(row['Leg1_Hrs'])})</span></div>
                      <div class="tc-muted" style="font-size:12px;">Runs: {row['Train_1_Running_Days']}</div>
                    </div>
                    <div style="opacity:0.45; align-self:center;">⇄</div>
                    <div>
                      <div class="tc-muted" style="font-size:11px;">TRAIN 2 · {row['Train_2_No']}</div>
                      <div><b>{row['Train_2_Name']}</b></div>
                      <div>{row['Leave_Mid']} → {row['Arrive_End']}
                           <span class="tc-muted">({format_duration(row['Leg2_Hrs'])})</span></div>
                      <div class="tc-muted" style="font-size:12px;">Runs: {row['Train_2_Running_Days']}</div>
                    </div>
                  </div>
                  {also_html}
                </div>
                """,
                unsafe_allow_html=True,
            )

            if int(row.get("Train2_Day_Offset", 0) or 0) > 0:
                st.caption(
                    f"Train 2 is on travel day +{int(row['Train2_Day_Offset'])} "
                    "(because of wait time or overnight)."
                )

            warning = str(row.get("Change_Warning") or "").strip()
            if warning:
                st.warning(warning)

            types_line = []
            if row.get("Train_1_Type"):
                types_line.append(f"T1: {row['Train_1_Type']}")
            if row.get("Train_2_Type"):
                types_line.append(f"T2: {row['Train_2_Type']}")
            if row.get("Train_1_Halt_Min") is not None and pd.notna(row.get("Train_1_Halt_Min")):
                types_line.append(f"T1 halt {int(row['Train_1_Halt_Min'])}m")
            if row.get("Train_2_Halt_Min") is not None and pd.notna(row.get("Train_2_Halt_Min")):
                types_line.append(f"T2 halt {int(row['Train_2_Halt_Min'])}m")
            if types_line:
                st.caption(" · ".join(types_line))

            board_date = _parse_board_date(row) or search_date
            leg_start = extract_code(str(row.get("Start_From") or "")) or start_code
            leg_end = extract_code(str(row.get("End_At") or "")) or end_code
            cache_key = connection_cache_key(row, start_code, end_code, search_date, quota)

            seat_col = st.columns(1)[0]
            with seat_col:
                scrape_disabled = board_date is None
                if st.button(
                    "Check seats (both trains)",
                    key=f"seats_{i}_{row['Train_1_No']}_{row['Train_2_No']}",
                    use_container_width=True,
                    disabled=scrape_disabled,
                    help="Gets live seats for Train 1 and Train 2 together. Needs a travel date.",
                ):
                    with st.spinner("Getting seats for both trains…"):
                        raw = fetch_connection_availability(
                            start_code=leg_start,
                            via_code=str(row["Via_Code"]),
                            end_code=leg_end,
                            train1_no=str(row["Train_1_No"]),
                            train2_no=str(row["Train_2_No"]),
                            journey_date=board_date,
                            train2_day_offset=int(row.get("Train2_Day_Offset", 0) or 0),
                            quota=quota,
                        )
                        date2 = board_date + datetime.timedelta(
                            days=int(row.get("Train2_Day_Offset", 0) or 0)
                        )
                        raw["leg1"] = _enrich_seat_result(
                            raw.get("leg1") or {},
                            board_date,
                            str(row.get("Leave_Start") or ""),
                        )
                        raw["leg2"] = _enrich_seat_result(
                            raw.get("leg2") or {},
                            date2,
                            str(row.get("Leave_Mid") or ""),
                        )
                        st.session_state.seat_cache[cache_key] = raw
                    st.rerun()
                if scrape_disabled:
                    st.caption("Choose a travel date above to check seats.")
                elif board_date:
                    st.caption(
                        f"Will scrape seats for **{board_date.strftime('%a %d %b %Y')}** "
                        f"(Train 1) and matching date for Train 2."
                    )

            seat_data = st.session_state.seat_cache.get(cache_key)
            if seat_data:
                st.markdown("##### Seats for both trains")
                a, b = st.columns(2)
                with a:
                    render_seat_block(seat_data.get("leg1") or {}, "Train 1", pref_classes=pref_classes)
                with b:
                    render_seat_block(seat_data.get("leg2") or {}, "Train 2", pref_classes=pref_classes)
                scored = score_connection_seats(seat_data, pref_classes)
                if scored["seat_score"] >= 0:
                    fare_txt = (
                        f" · est. ₹{int(scored['est_fare'])}"
                        if scored.get("est_fare") is not None
                        else ""
                    )
                    st.caption(
                        f"Seat score (both legs): **{scored['seat_score']:.0f}/100**{fare_txt}"
                    )
                st.caption(
                    "Seat info is live from ConfirmTkt. Always confirm on IRCTC before booking."
                )


def render_two_change_cards(results: pd.DataFrame, start_label: str, end_label: str, limit: int = 8):
    """Display optional 2-change (3-train) itineraries."""
    if results is None or results.empty:
        return
    st.subheader("2-change options")
    st.caption(
        f"Showing top {min(limit, len(results))} of {len(results)}. "
        "These use two mid-stations (three trains). Seat check is per leg on IRCTC."
    )
    for i, (_, row) in enumerate(results.head(limit).iterrows()):
        title = (
            f"#{i + 1} · {row['Via_Station']} · "
            f"{format_duration(row['Total_Hrs'])} total · "
            f"waits {format_duration(row['Layover1_Hrs'])}+{format_duration(row['Layover2_Hrs'])}"
        )
        with st.expander(title, expanded=(i < 1)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", format_duration(row["Total_Hrs"]))
            c2.metric("Wait 1", format_duration(row["Layover1_Hrs"]))
            c3.metric("Wait 2", format_duration(row["Layover2_Hrs"]))
            c4.metric("Changes", 2)
            board_on = row.get("Board_On") or ""
            st.markdown(
                f"""
                <div class="tc-route-box">
                  <div class="tc-muted" style="margin-bottom:6px;">
                    {start_label} → <b>{row.get('Via_Station','')}</b> → {end_label}
                    {f" · Board <b>{board_on}</b>" if board_on else ""}
                  </div>
                  <div style="font-size:14px; display:flex; flex-direction:column; gap:10px;">
                    <div><span class="tc-muted">TRAIN 1 · {row['Train_1_No']}</span>
                      <b> {row['Train_1_Name']}</b> ({row.get('Train_1_Type','')})
                      · {row['Leave_Start']} → {row['Arrive_Mid']}
                      <span class="tc-muted">({format_duration(row['Leg1_Hrs'])})</span></div>
                    <div><span class="tc-muted">TRAIN 2 · {row['Train_2_No']}</span>
                      <b> {row['Train_2_Name']}</b> ({row.get('Train_2_Type','')})
                      · {row['Leave_Mid']} → {row['Arrive_Via2']}
                      <span class="tc-muted">({format_duration(row['Leg2_Hrs'])})</span></div>
                    <div><span class="tc-muted">TRAIN 3 · {row['Train_3_No']}</span>
                      <b> {row['Train_3_Name']}</b> ({row.get('Train_3_Type','')})
                      · {row['Leave_Via2']} → {row['Arrive_End']}
                      <span class="tc-muted">({format_duration(row['Leg3_Hrs'])})</span></div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            warn = str(row.get("Change_Warning") or "").strip()
            if warn:
                st.warning(warn)


@st.cache_data(show_spinner=False)
def cached_via_options(start_code, end_code, include_nearby=False):
    return get_possible_via_stations(start_code, end_code, include_nearby=include_nearby)


@st.cache_data(show_spinner=False)
def cached_search_trains(query):
    return search_trains(query)


@st.cache_data(show_spinner=False)
def cached_hubs(start_code, end_code, include_nearby=False):
    return get_top_via_hubs(start_code, end_code, include_nearby=include_nearby)


# =====================================================================================
# HEADER + THEME TOGGLE
# =====================================================================================
bootstrap_from_query_params()
apply_theme_css(st.session_state.theme)
dark = st.session_state.theme == "dark"

head_l, head_r = st.columns([6, 2])
with head_l:
    st.markdown(
        """
        <div class="tc-hero">
          <h1 class="tc-brand">Train Connector</h1>
          <p class="tc-sub">
            Find connecting trains, direct trains, and check seats for both trains in one go.
          </p>
          <div class="tc-chip-row">
            <span class="tc-chip">Connections</span>
            <span class="tc-chip">Direct trains</span>
            <span class="tc-chip">Seat check</span>
            <span class="tc-chip">Station explorer</span>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
with head_r:
    st.write("")
    st.write("")
    theme_label = "Light mode" if dark else "Dark mode"
    if st.button(theme_label, use_container_width=True, key="theme_toggle"):
        st.session_state.theme = "light" if dark else "dark"
        st.rerun()
    st.caption("Switch theme anytime.")

tab_connect, tab_direct, tab_station, tab_lookup, tab_about = st.tabs(
    [
        "Find Connections",
        "Direct Trains",
        "Station Explorer",
        "Train Lookup",
        "About / Data",
    ]
)

# =====================================================================================
# TAB 1: FIND CONNECTIONS
# =====================================================================================
with tab_connect:
    # Recent searches
    if st.session_state.saved_searches:
        st.caption("Recent searches")
        cols = st.columns(min(5, len(st.session_state.saved_searches)))
        for i, saved in enumerate(st.session_state.saved_searches):
            with cols[i]:
                if st.button(
                    saved.get("label") or f"Search {i+1}",
                    key=f"saved_search_{i}",
                    use_container_width=True,
                ):
                    st.session_state.swap_tick += 1
                    tick = st.session_state.swap_tick
                    st.session_state[f"start_{tick}"] = saved.get("start_station") or ""
                    st.session_state[f"end_{tick}"] = saved.get("end_station") or ""
                    if saved.get("search_date"):
                        st.session_state["conn_use_date"] = True
                        st.session_state["conn_travel_date"] = saved["search_date"]
                    st.session_state["conn_flex_on"] = bool(saved.get("flexible"))
                    st.session_state["conn_nearby"] = bool(saved.get("include_nearby"))
                    st.session_state["conn_pref_classes"] = list(saved.get("pref_classes") or [])
                    st.session_state["conn_train_types"] = list(saved.get("train_types") or [])
                    st.session_state["conn_two_change"] = bool(saved.get("allow_two_change"))
                    if saved.get("quota"):
                        st.session_state["conn_quota"] = saved["quota"]
                    st.rerun()

    col1, col_swap, col2 = st.columns([5, 1, 5])
    with col1:
        start_station = st.selectbox(
            "Start Station",
            options=[""] + station_list,
            index=0,
            key=f"start_{st.session_state.swap_tick}",
            help="Type or select the city/station you are starting from.",
        )
    with col_swap:
        st.write("")
        st.write("")
        if st.button("⇄", help="Swap start and end", use_container_width=True):
            a = st.session_state.get(f"start_{st.session_state.swap_tick}", "")
            b = st.session_state.get(f"end_{st.session_state.swap_tick}", "")
            st.session_state.swap_tick += 1
            st.session_state[f"start_{st.session_state.swap_tick}"] = b
            st.session_state[f"end_{st.session_state.swap_tick}"] = a
            st.rerun()
    with col2:
        end_station = st.selectbox(
            "End Station",
            options=[""] + station_list,
            index=0,
            key=f"end_{st.session_state.swap_tick}",
            help="Type or select your final destination.",
        )

    s_code_live = extract_code(start_station) if start_station else None
    e_code_live = extract_code(end_station) if end_station else None

    # ---- BIG JOURNEY DATE ----
    st.markdown(
        """
        <div class="tc-date-panel">
          <div class="tc-date-label">Travel date</div>
          <div class="tc-date-hint">
            Needed to filter trains by day and to check seats for both trains.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    dcol1, dcol2, dcol3 = st.columns([3, 2, 2])
    with dcol1:
        use_date = st.toggle("Use travel date", value=True, key="conn_use_date")
        search_date = None
        conn_flex = 0
        if use_date:
            search_date = st.date_input(
                "Travel date",
                value=datetime.date.today(),
                min_value=datetime.date.today(),
                max_value=datetime.date.today() + datetime.timedelta(days=120),
                key="conn_travel_date",
                label_visibility="collapsed",
            )
            conn_flexible = st.toggle(
                "Flexible dates (±3 days)",
                value=False,
                key="conn_flex_on",
                help="Also show connections that work up to 3 days before or after your date.",
            )
            conn_flex = 3 if conn_flexible else 0
    with dcol2:
        if use_date and search_date:
            st.metric("Weekday", search_date.strftime("%A"))
        else:
            st.metric("Weekday", "Any day")
        include_nearby = st.toggle(
            "Include nearby stations",
            value=False,
            key="conn_nearby",
            help="Also search alternate terminals in the same city (e.g. NDLS ↔ NZM ↔ ANVT).",
        )
    with dcol3:
        quota = st.selectbox(
            "Quota (for seats)",
            options=["GN", "TQ", "PT", "LD", "SS"],
            index=0,
            help="Used when checking seats for both trains.",
            key="conn_quota",
        )
        pref_classes = st.multiselect(
            "Preferred class",
            options=TRAVEL_CLASSES,
            default=[],
            key="conn_pref_classes",
            help="Filter seat pills and score routes by these classes (e.g. 3A, SL).",
        )

    if include_nearby and s_code_live:
        near = get_nearby_station_codes(s_code_live, include_nearby=True)
        if len(near) > 1:
            st.caption(
                "Nearby from: "
                + ", ".join(nearby_station_labels(s_code_live)[:8])
            )
    if include_nearby and e_code_live:
        near_e = get_nearby_station_codes(e_code_live, include_nearby=True)
        if len(near_e) > 1:
            st.caption(
                "Nearby to: "
                + ", ".join(nearby_station_labels(e_code_live)[:8])
            )

    with st.expander("Advanced filters", expanded=False):
        col3, col4 = st.columns(2)
        with col3:
            use_via = st.checkbox(
                "Route via specific mid-station",
                help="Unchecked = search all intersecting stations automatically.",
            )
            via_station = None
            if use_via:
                smart_via_list = []
                if s_code_live and e_code_live and s_code_live != e_code_live:
                    smart_via_list = cached_via_options(
                        s_code_live, e_code_live, include_nearby=include_nearby
                    )
                if smart_via_list:
                    via_station = st.selectbox(
                        "Select Mid-Station",
                        options=[""] + smart_via_list,
                        index=0,
                    )
                else:
                    if s_code_live and e_code_live:
                        st.caption("No route-specific mid-stations found yet — showing full list.")
                    via_station = st.selectbox(
                        "Select Mid-Station",
                        options=[""] + station_list,
                        index=0,
                    )

            prefer_via = st.multiselect(
                "Prefer these hubs (soft pin)",
                options=station_list,
                default=[],
                key="conn_prefer_via",
                help="Boost routes that change at these stations when available.",
                max_selections=5,
            )
            avoid_via = st.multiselect(
                "Avoid changing at",
                options=station_list,
                default=[],
                key="conn_avoid_via",
                help="Never show connections that interchange at these stations.",
                max_selections=8,
            )

        with col4:
            use_max_wait = st.checkbox(
                "Set waiting time range",
                help="Default wait window is 1–12 hours.",
            )
            min_wait, max_wait = 1, 12
            if use_max_wait:
                min_wait, max_wait = st.slider(
                    "Wait window (Hours)",
                    min_value=0,
                    max_value=24,
                    value=(1, 8),
                )
            train_types = st.multiselect(
                "Train types (both legs must match)",
                options=TRAIN_TYPE_OPTIONS,
                default=[],
                key="conn_train_types",
                help="e.g. select Express + Vande Bharat + Rajdhani to allow those mixes.",
            )
            allow_two_change = st.toggle(
                "Also search 2-change routes",
                value=False,
                key="conn_two_change",
                help="Optional slower search: start → hub1 → hub2 → end (3 trains).",
            )

        col5, col6 = st.columns(2)
        with col5:
            use_dep_time = st.checkbox("Restrict departure time")
            dep_after = None
            dep_before = None
            if use_dep_time:
                dep_after_ui = st.time_input("Don't leave before", value=None, key="conn_dep_after")
                dep_before_ui = st.time_input("Don't leave after", value=None, key="conn_dep_before")
                if dep_after_ui:
                    dep_after = dep_after_ui.strftime("%H:%M:%S")
                if dep_before_ui:
                    dep_before = dep_before_ui.strftime("%H:%M:%S")
        with col6:
            use_arr_time = st.checkbox("Restrict arrival time")
            arr_after = None
            arr_before = None
            if use_arr_time:
                arr_after_ui = st.time_input("Don't arrive before", value=None, key="conn_arr_after")
                arr_before_ui = st.time_input("Don't arrive after", value=None, key="conn_arr_before")
                if arr_after_ui:
                    arr_after = arr_after_ui.strftime("%H:%M:%S")
                if arr_before_ui:
                    arr_before = arr_before_ui.strftime("%H:%M:%S")

        col7, col8, col9 = st.columns(3)
        with col7:
            sort_choice = st.radio(
                "Sort results by",
                options=[
                    "Fastest total journey",
                    "Shortest wait",
                    "Earliest departure",
                    "Best seats (after check)",
                    "Cheapest (after check)",
                ],
                index=0,
                key="conn_sort_choice",
            )
            sort_by = {
                "Fastest total journey": "fastest",
                "Shortest wait": "layover",
                "Earliest departure": "departure",
                "Best seats (after check)": "seats",
                "Cheapest (after check)": "fare",
            }[sort_choice]
        with col8:
            exclude_overnight = st.checkbox(
                "Hide overnight waits",
                help="Hide connections with a long night wait at the mid station.",
            )
            max_results = st.number_input(
                "Max results",
                min_value=10,
                max_value=500,
                value=100,
                step=10,
            )
        with col9:
            view_mode = st.radio(
                "Results view",
                options=["Cards + table", "Cards only", "Table only"],
                index=0,
            )

    if st.button("Find Connections", type="primary", use_container_width=True):
        if not start_station or not end_station:
            st.error("Please select both a Departure and Arrival station.")
        elif start_station == end_station:
            st.warning("Departure and Arrival stations cannot be the same.")
        else:
            engine_sort = sort_by if sort_by in ("fastest", "layover", "departure") else "fastest"
            with st.spinner("Searching connecting routes..."):
                s_code = extract_code(start_station)
                e_code = extract_code(end_station)
                v_code = extract_code(via_station) if use_via and via_station else None
                prefer_codes = [extract_code(x) for x in (prefer_via or []) if extract_code(x)]
                avoid_codes = [extract_code(x) for x in (avoid_via or []) if extract_code(x)]

                directs = find_direct_trains(
                    s_code,
                    e_code,
                    search_date=search_date,
                    flexible_days=conn_flex,
                    include_nearby=include_nearby,
                    train_types=train_types or None,
                )
                results = find_connections_pro(
                    start_code=s_code,
                    end_code=e_code,
                    via_code=v_code,
                    min_layover_hrs=min_wait,
                    max_layover_hrs=max_wait,
                    pref_dep_after=dep_after,
                    pref_dep_before=dep_before,
                    pref_arr_after=arr_after,
                    pref_arr_before=arr_before,
                    search_date=search_date,
                    flexible_days=conn_flex,
                    include_nearby=include_nearby,
                    sort_by=engine_sort,
                    exclude_overnight=exclude_overnight,
                    max_results=int(max_results),
                    train_types=train_types or None,
                    avoid_via_codes=avoid_codes or None,
                    prefer_via_codes=prefer_codes or None,
                )
                two_change = None
                if allow_two_change:
                    two_change = find_two_change_connections(
                        start_code=s_code,
                        end_code=e_code,
                        min_layover_hrs=max(0.5, float(min_wait)),
                        max_layover_hrs=min(12.0, float(max_wait)),
                        search_date=search_date,
                        flexible_days=conn_flex,
                        include_nearby=include_nearby,
                        train_types=train_types or None,
                        avoid_via_codes=avoid_codes or None,
                        hub_limit=8,
                        max_results=25,
                        sort_by=engine_sort,
                    )

            meta = {
                "start_station": start_station,
                "end_station": end_station,
                "s_code": s_code,
                "e_code": e_code,
                "search_date": search_date,
                "quota": quota,
                "view_mode": view_mode,
                "sort_by": sort_by,
                "pref_classes": list(pref_classes or []),
                "train_types": list(train_types or []),
                "include_nearby": bool(include_nearby),
                "flexible": bool(conn_flex),
                "allow_two_change": bool(allow_two_change),
                "direct_count": 0 if directs is None or directs.empty else len(directs),
            }
            st.session_state.conn_results = results
            st.session_state.conn_meta = meta
            st.session_state.conn_directs = (
                None if directs is None or directs.empty else directs
            )
            st.session_state.two_change_results = (
                None if two_change is None or two_change.empty else two_change
            )
            st.session_state.show_direct_from_conn = False
            st.session_state.seat_cache = {}
            st.session_state.direct_seat_cache = {}
            update_search_query_params(meta)
            remember_search(meta)

    meta = st.session_state.conn_meta or {}
    results = st.session_state.conn_results
    two_change = st.session_state.two_change_results
    pref_classes_meta = meta.get("pref_classes") or pref_classes or []
    sort_by_meta = meta.get("sort_by") or "fastest"

    if results is not None or two_change is not None:
        if meta.get("direct_count"):
            st.info(
                f"**{meta['direct_count']} direct train(s)** also run between these stations "
                "on your selected travel date"
                + (" (± flexible days)." if meta.get("flexible") else " (exact / best nearby date).")
            )
            if st.button(
                "View direct trains & check seats →",
                key="open_direct_from_conn",
                type="primary",
            ):
                st.session_state.show_direct_from_conn = True
                # Prefill Direct Trains tab for later
                st.session_state["direct_start"] = meta.get("start_station", "")
                st.session_state["direct_end"] = meta.get("end_station", "")
                if meta.get("search_date"):
                    st.session_state["direct_date_on"] = True
                    st.session_state["direct_date"] = meta["search_date"]
                    st.session_state["direct_flex_on"] = bool(meta.get("flexible"))
                st.session_state["direct_nearby"] = bool(meta.get("include_nearby"))
                st.session_state["direct_pref_classes"] = list(pref_classes_meta)
                st.session_state.direct_results = st.session_state.get("conn_directs")
                st.session_state.direct_meta = {
                    "start_station": meta.get("start_station"),
                    "end_station": meta.get("end_station"),
                    "s_code": meta.get("s_code"),
                    "e_code": meta.get("e_code"),
                    "search_date": meta.get("search_date"),
                    "quota": meta.get("quota") or "GN",
                    "flexible": bool(meta.get("flexible")),
                    "include_nearby": bool(meta.get("include_nearby")),
                    "pref_classes": list(pref_classes_meta),
                }
                st.rerun()

            if st.session_state.show_direct_from_conn and st.session_state.get("conn_directs") is not None:
                render_direct_train_cards(
                    st.session_state.conn_directs,
                    meta["s_code"],
                    meta["e_code"],
                    meta["start_station"],
                    meta["end_station"],
                    quota=meta.get("quota") or "GN",
                    key_prefix="conn_direct",
                    pref_classes=pref_classes_meta,
                )
                st.caption("Tip: the same search is also saved under the **Direct Trains** tab.")

        if results is not None and results.empty and (two_change is None or two_change.empty):
            st.warning(
                "No connections found matching your filters. "
                "Try widening the wait time, allowing overnight waits, "
                "turning on nearby stations / flexible dates, "
                "adding more train types, or clearing time/date filters."
            )
        elif results is not None and not results.empty:
            # Refresh seat scores from cache, then sort
            results = apply_seat_scores_to_results(
                results,
                meta["s_code"],
                meta["e_code"],
                meta.get("search_date"),
                meta.get("quota") or "GN",
                pref_classes_meta,
            )
            results = sort_results_df(results, sort_by_meta)
            st.session_state.conn_results = results

            st.success(f"Found {len(results)} one-change connection(s)!")

            fastest_time = results["Total_Hrs"].min()
            best_layover = results["Layover_Hrs"].min()
            via_count = results["Via_Code"].nunique()

            m1, m2, m3, m4 = st.columns(4)
            m1.metric("Connections", len(results))
            m2.metric("Fastest", format_duration(fastest_time))
            m3.metric("Shortest wait", format_duration(best_layover))
            m4.metric("Via hubs", via_count)

            # Share + export + bulk seat check
            share = build_share_url(meta)
            x1, x2, x3 = st.columns(3)
            with x1:
                st.download_button(
                    "Download CSV",
                    data=results.to_csv(index=False).encode("utf-8"),
                    file_name=(
                        f"connections_{meta.get('s_code', 'from')}_"
                        f"{meta.get('e_code', 'to')}.csv"
                    ),
                    mime="text/csv",
                    use_container_width=True,
                    key="conn_csv_dl",
                )
            with x2:
                itinerary = format_connection_itinerary(
                    results,
                    meta.get("start_station", ""),
                    meta.get("end_station", ""),
                    limit=5,
                )
                st.download_button(
                    "Download itinerary (.txt)",
                    data=itinerary.encode("utf-8"),
                    file_name="train_connector_itinerary.txt",
                    mime="text/plain",
                    use_container_width=True,
                    key="conn_itin_dl",
                )
            with x3:
                bulk_disabled = meta.get("search_date") is None
                if st.button(
                    "Check seats for top 5",
                    use_container_width=True,
                    disabled=bulk_disabled,
                    key="bulk_seat_top5",
                    help="Fetches seats for the first 5 routes so you can sort by availability or fare.",
                ):
                    with st.spinner("Checking seats for top 5 connections…"):
                        for _, row in results.head(5).iterrows():
                            key = connection_cache_key(
                                row,
                                meta["s_code"],
                                meta["e_code"],
                                meta.get("search_date"),
                                meta.get("quota") or "GN",
                            )
                            if key in st.session_state.seat_cache:
                                continue
                            board_date = _parse_board_date(row) or meta.get("search_date")
                            if board_date is None:
                                continue
                            leg_start = extract_code(str(row.get("Start_From") or "")) or meta["s_code"]
                            leg_end = extract_code(str(row.get("End_At") or "")) or meta["e_code"]
                            raw = fetch_connection_availability(
                                start_code=leg_start,
                                via_code=str(row["Via_Code"]),
                                end_code=leg_end,
                                train1_no=str(row["Train_1_No"]),
                                train2_no=str(row["Train_2_No"]),
                                journey_date=board_date,
                                train2_day_offset=int(row.get("Train2_Day_Offset", 0) or 0),
                                quota=meta.get("quota") or "GN",
                            )
                            date2 = board_date + datetime.timedelta(
                                days=int(row.get("Train2_Day_Offset", 0) or 0)
                            )
                            raw["leg1"] = _enrich_seat_result(
                                raw.get("leg1") or {},
                                board_date,
                                str(row.get("Leave_Start") or ""),
                            )
                            raw["leg2"] = _enrich_seat_result(
                                raw.get("leg2") or {},
                                date2,
                                str(row.get("Leave_Mid") or ""),
                            )
                            st.session_state.seat_cache[key] = raw
                    st.rerun()

            if share:
                st.text_input(
                    "Shareable search link (copy from address bar, or use this query)",
                    value=share,
                    key="conn_share_link",
                    help="Open this app with these query params to restore the search.",
                )
            with st.expander("Copy itinerary text"):
                st.code(
                    format_connection_itinerary(
                        results,
                        meta.get("start_station", ""),
                        meta.get("end_station", ""),
                        limit=5,
                    ),
                    language=None,
                )

            if sort_by_meta in ("seats", "fare") and (
                "Seat_Score" not in results.columns or results["Seat_Score"].isna().all()
            ):
                st.info(
                    "Seat / fare sorting needs live data — click **Check seats for top 5** first "
                    "(or check individual routes)."
                )

            if meta.get("search_date"):
                unknown_count = (
                    (results["Train_1_Running_Days"] == "Unknown")
                    | (results["Train_2_Running_Days"] == "Unknown")
                ).sum()
                if unknown_count:
                    st.caption(
                        f"{unknown_count} route(s) include a train with no running-days "
                        "data — assumed to run; verify before booking."
                    )

            with st.expander("Top interchange hubs for this route"):
                hubs = cached_hubs(
                    meta["s_code"],
                    meta["e_code"],
                    include_nearby=bool(meta.get("include_nearby")),
                )
                if hubs.empty:
                    st.caption("No hub breakdown available.")
                else:
                    st.dataframe(hubs, use_container_width=True, hide_index=True)

            view_mode = meta.get("view_mode", "Cards + table")
            if view_mode in ("Cards + table", "Cards only"):
                render_connection_cards(
                    results,
                    meta["start_station"],
                    meta["end_station"],
                    meta["s_code"],
                    meta["e_code"],
                    meta.get("search_date"),
                    meta.get("quota") or "GN",
                    dark,
                    pref_classes=pref_classes_meta,
                )

            if view_mode in ("Cards + table", "Table only"):
                st.subheader("Full results table")
                st.dataframe(
                    results,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Total_Hrs": st.column_config.ProgressColumn(
                            "Total Journey (Hrs)",
                            help="Total time from start to finish",
                            format="%f",
                            min_value=0,
                            max_value=float(results["Total_Hrs"].max()),
                        ),
                        "Layover_Hrs": st.column_config.NumberColumn(
                            "Wait", help="Waiting time at mid-station", format="%.1f"
                        ),
                        "Leg1_Hrs": st.column_config.NumberColumn("Train 1 (Hrs)", format="%.1f"),
                        "Leg2_Hrs": st.column_config.NumberColumn("Train 2 (Hrs)", format="%.1f"),
                        "Comfort": st.column_config.TextColumn("Comfort"),
                        "Overnight_Layover": st.column_config.TextColumn("Overnight"),
                        "Train_1_Running_Days": st.column_config.TextColumn("Train 1 Runs On"),
                        "Train_2_Running_Days": st.column_config.TextColumn("Train 2 Runs On"),
                        "Seat_Score": st.column_config.NumberColumn(
                            "Seat score", help="Higher = easier to confirm (after seat check)"
                        ),
                        "Est_Fare": st.column_config.NumberColumn(
                            "Est. fare (₹)", format="%.0f"
                        ),
                        "Change_Warning": st.column_config.TextColumn("Change warning"),
                        "Train_1_Type": st.column_config.TextColumn("T1 type"),
                        "Train_2_Type": st.column_config.TextColumn("T2 type"),
                    },
                )

        if two_change is not None and not two_change.empty:
            st.success(f"Found {len(two_change)} two-change route(s).")
            st.download_button(
                "Download 2-change CSV",
                data=two_change.to_csv(index=False).encode("utf-8"),
                file_name=(
                    f"two_change_{meta.get('s_code', 'from')}_"
                    f"{meta.get('e_code', 'to')}.csv"
                ),
                mime="text/csv",
                use_container_width=True,
                key="two_change_csv_dl",
            )
            render_two_change_cards(
                two_change,
                meta.get("start_station") or "",
                meta.get("end_station") or "",
            )
            with st.expander("2-change results table"):
                st.dataframe(two_change, use_container_width=True, hide_index=True)
        elif meta.get("allow_two_change") and (two_change is None or two_change.empty):
            st.caption("No 2-change routes found within the hub / wait limits.")

# =====================================================================================
# TAB 2: DIRECT TRAINS
# =====================================================================================
with tab_direct:
    st.subheader("Direct trains (no change)")
    st.markdown("See if you can go **start → end on a single train** before looking for split tickets.")

    d1, d2 = st.columns(2)
    with d1:
        d_start = st.selectbox("From", options=[""] + station_list, index=0, key="direct_start")
    with d2:
        d_end = st.selectbox("To", options=[""] + station_list, index=0, key="direct_end")

    st.markdown(
        """
        <div class="tc-date-panel">
          <div class="tc-date-label">Travel date</div>
          <div class="tc-date-hint">
            Date you board at the From station. Flexible dates always check 3 days before and after.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    d3, d4 = st.columns(2)
    with d3:
        d_use_date = st.toggle("Filter by travel date", value=True, key="direct_date_on")
        d_date = None
        d_flex = 0
        if d_use_date:
            d_date = st.date_input(
                "Travel date",
                value=datetime.date.today(),
                min_value=datetime.date.today(),
                key="direct_date",
                label_visibility="collapsed",
            )
            d_flexible = st.toggle(
                "Flexible dates (±3 days)",
                value=True,
                key="direct_flex_on",
                help="Also show trains that work up to 3 days before or after your date.",
            )
            d_flex = 3 if d_flexible else 0
        d_nearby = st.toggle(
            "Include nearby stations",
            value=False,
            key="direct_nearby",
            help="Also search alternate terminals in the same city.",
        )
    with d4:
        d_sort = st.radio(
            "Sort by",
            options=["Fastest", "Earliest departure"],
            horizontal=True,
            key="direct_sort",
        )
        d_sort_by = "fastest" if d_sort == "Fastest" else "departure"
        d_quota = st.selectbox(
            "Quota (for seats)",
            options=["GN", "TQ", "PT", "LD", "SS"],
            index=0,
            key="direct_quota",
        )
        d_pref_classes = st.multiselect(
            "Preferred class",
            options=TRAVEL_CLASSES,
            key="direct_pref_classes",
        )
        d_train_types = st.multiselect(
            "Train types",
            options=TRAIN_TYPE_OPTIONS,
            key="direct_train_types",
            help="Only show trains of these types.",
        )

    if st.button("Search direct trains", type="primary", use_container_width=True, key="direct_btn"):
        if not d_start or not d_end:
            st.error("Select both stations.")
        elif d_start == d_end:
            st.warning("Stations must be different.")
        else:
            with st.spinner("Looking up direct trains..."):
                directs = find_direct_trains(
                    extract_code(d_start),
                    extract_code(d_end),
                    search_date=d_date,
                    flexible_days=d_flex if d_use_date else 0,
                    include_nearby=d_nearby,
                    train_types=d_train_types or None,
                    sort_by=d_sort_by,
                )
            st.session_state.direct_results = directs
            st.session_state.direct_meta = {
                "start_station": d_start,
                "end_station": d_end,
                "s_code": extract_code(d_start),
                "e_code": extract_code(d_end),
                "search_date": d_date,
                "quota": d_quota,
                "flexible": bool(d_flex),
                "include_nearby": bool(d_nearby),
                "pref_classes": list(d_pref_classes or []),
            }
            st.session_state.direct_seat_cache = {}

    d_meta = st.session_state.direct_meta or {}
    directs = st.session_state.direct_results

    if directs is not None:
        if directs.empty:
            st.warning("No direct trains found. Try the Connections tab for routes with a change.")
        else:
            st.success(f"{len(directs)} direct train(s) found.")
            m1, m2 = st.columns(2)
            m1.metric("Trains", len(directs))
            m2.metric("Fastest", format_duration(directs["Duration_Hrs"].min()))

            st.download_button(
                "Download CSV",
                data=directs.to_csv(index=False).encode("utf-8"),
                file_name=(
                    f"direct_{d_meta.get('s_code', 'from')}_{d_meta.get('e_code', 'to')}.csv"
                ),
                mime="text/csv",
                use_container_width=True,
                key="direct_csv_dl",
            )

            if d_meta.get("search_date"):
                st.caption(
                    "Board on = date at your From station. "
                    "Leaves origin on = weekday the train starts from its first station."
                )

            render_direct_train_cards(
                directs,
                d_meta.get("s_code") or extract_code(d_start),
                d_meta.get("e_code") or extract_code(d_end),
                d_meta.get("start_station") or d_start,
                d_meta.get("end_station") or d_end,
                quota=d_meta.get("quota") or d_quota,
                key_prefix="direct_tab",
                pref_classes=d_meta.get("pref_classes") or d_pref_classes,
            )

# =====================================================================================
# TAB 3: STATION EXPLORER
# =====================================================================================
with tab_station:
    st.subheader("Station explorer")
    st.markdown("See every train that stops at a station — useful when planning a change.")

    st_pick = st.selectbox("Station", options=[""] + station_list, index=0, key="station_pick")

    t1, t2 = st.columns(2)
    with t1:
        use_dep_window = st.checkbox("Filter by departure window", key="stn_win")
    dep_a = dep_b = None
    with t2:
        if use_dep_window:
            da = st.time_input("Depart after", value=datetime.time(6, 0), key="stn_after")
            db = st.time_input("Depart before", value=datetime.time(22, 0), key="stn_before")
            dep_a = da.strftime("%H:%M:%S") if da else None
            dep_b = db.strftime("%H:%M:%S") if db else None

    if st_pick and st.button("Show trains at station", type="primary", use_container_width=True):
        code = extract_code(st_pick)
        with st.spinner("Loading trains..."):
            trains = get_trains_at_station(code, time_after=dep_a, time_before=dep_b)
        if trains.empty:
            st.warning("No trains found for this station / time window.")
        else:
            st.success(f"{len(trains)} train(s) stop at **{st_pick}**.")
            st.dataframe(trains, use_container_width=True, hide_index=True)

# =====================================================================================
# TAB 4: TRAIN LOOKUP
# =====================================================================================
with tab_lookup:
    st.subheader("Look up a train")
    st.markdown("Search by **train number** (e.g. `12213`) or **train name** (e.g. `duronto`).")

    query = st.text_input("Search train number or name", placeholder="e.g. 12213 or Duronto")

    selected_train_number = None

    if query:
        matches = cached_search_trains(query)
        if not matches:
            st.warning("No trains matched that search. Try a different number or name.")
        else:
            options = [f"{tn} — {name}" if name else tn for tn, name in matches]
            choice = st.selectbox("Matching trains", options=options, index=0)
            selected_train_number = choice.split(" — ")[0].strip()

    if selected_train_number:
        summary = get_train_summary(selected_train_number)
        if summary is None:
            st.error("Couldn't find schedule details for this train.")
        else:
            st.markdown("---")
            st.markdown(f"### {summary['train_number']} — {summary['train_name']}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("From", summary["source"])
            c2.metric("To", summary["destination"])
            c3.metric("Total Stops", summary["total_stops"])
            c4.metric("Total Duration", format_duration(summary["total_duration_hrs"]))

            st.markdown("**Runs on:**")
            render_running_day_badges(summary["running_days_bits"], dark)

            st.link_button(
                "Open on IRCTC",
                summary.get("irctc_url") or irctc_train_url(selected_train_number),
                use_container_width=False,
            )

            st.markdown("---")
            st.markdown("**Full Schedule**")
            schedule = get_train_schedule(selected_train_number)
            st.dataframe(
                schedule,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Halt (min)": st.column_config.NumberColumn("Halt (min)", format="%.0f min"),
                },
            )
            # Highlight very short halts
            if not schedule.empty and "Halt (min)" in schedule.columns:
                short = schedule[schedule["Halt (min)"] <= 2]
                if not short.empty:
                    bits = [
                        f"{r['Station']} ({int(r['Halt (min)'])}m)"
                        for _, r in short.iterrows()
                    ]
                    st.caption("Short halts (≤2 min): " + ", ".join(bits[:12]))

# =====================================================================================
# TAB 5: ABOUT / DATA
# =====================================================================================
with tab_about:
    st.subheader("About Train Connector")
    st.markdown(
        """
        Plan **direct** and **connecting** journeys on Indian Railways schedules,
        then check live seats for both legs of a change.

        This is a planning aid — always confirm on IRCTC before booking.
        """
    )
    stats = get_data_stats()
    a1, a2, a3, a4 = st.columns(4)
    a1.metric("Trains in schedule", f"{stats['trains']:,}")
    a2.metric("Schedule rows", f"{stats['schedule_rows']:,}")
    a3.metric("Stations (schedule)", f"{stats['stations_in_schedule']:,}")
    a4.metric("Stations (master)", f"{stats['stations_master']:,}")

    b1, b2 = st.columns(2)
    b1.metric("Running-days known", f"{stats['running_days_known']:,}")
    b2.metric("Running-days unknown / assumed", f"{stats['running_days_unknown']:,}")

    st.markdown("#### Train types in this dataset")
    type_df = pd.DataFrame(
        [{"Type": k, "Trains": v} for k, v in (stats.get("train_types") or {}).items()]
    )
    if not type_df.empty:
        st.dataframe(type_df, use_container_width=True, hide_index=True)

    from pathlib import Path

    st.markdown("#### Data files")
    data_files = [
        "stations.csv",
        "train_schedule_scrapped.csv",
        "running_days_scrapped.csv",
    ]
    rows = []
    for name in data_files:
        p = Path(name)
        if p.exists():
            mtime = datetime.datetime.fromtimestamp(p.stat().st_mtime)
            rows.append(
                {
                    "File": name,
                    "Size": f"{p.stat().st_size / 1024:.1f} KB",
                    "Last modified": mtime.strftime("%Y-%m-%d %H:%M"),
                }
            )
        else:
            rows.append({"File": name, "Size": "—", "Last modified": "missing"})
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.markdown(
        """
        #### Caveats
        - Schedules are **scraped / offline** and may be incomplete or out of date.
        - Missing running-day bits are treated as **daily**.
        - **2-change** search uses a capped hub set (fast, not exhaustive).
        - Seat availability uses ConfirmTkt (unofficial) — verify on IRCTC.
        - Short platform halts (≤2–5 min) are flagged as tight changes.
        """
    )

st.markdown("---")
st.info(
    """
**Notes**
- If running-day data is missing, the train is treated as running every day.
- **Nearby stations** expand major metros (Delhi, Mumbai, Kolkata, Chennai, Bengaluru, Hyderabad).
- **Preferred class** filters seat pills and powers Best seats / Cheapest sorting after a seat check.
- **Train types / avoid-via / 2-change** live under Advanced filters.
- Recent searches appear above the station pickers (last 5).
- Share a search with query params like `?from=NDLS&to=HWH&date=2026-08-25&nearby=1&class=3A,SL`.
- Seat info comes from ConfirmTkt — always confirm on IRCTC before booking.
- Prices and seats can change; this app is only for planning.
"""
)

st.markdown(
    """
    <div class='tc-footer'>
        <p>Suggestions: <b>wazirnoob@gmail.com</b></p>
        <p>© 2026 Train Connector</p>
    </div>
    """,
    unsafe_allow_html=True,
)
