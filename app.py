import datetime
import streamlit as st
import pandas as pd

try:
    from engine import (
        find_connections_pro,
        find_direct_trains,
        get_possible_via_stations,
        get_trains_at_station,
        get_top_via_hubs,
        get_data_stats,
        station_list,
        search_trains,
        get_train_schedule,
        get_train_summary,
        format_duration,
        irctc_train_url,
        WEEKDAYS,
    )
except FileNotFoundError as e:
    st.set_page_config(page_title="Train Connector", layout="wide")
    st.error(
        f"Couldn't load one of the data files: **{e}**.\n\n"
        "Make sure `stations.csv`, `train_schedule_scrapped.csv` and "
        "`running_days_scrapped.csv` are all present in the app folder."
    )
    st.stop()


def extract_code(station_string):
    """'NEW DELHI (NDLS)' -> 'NDLS'"""
    if station_string:
        return station_string.split("(")[-1].replace(")", "").strip()
    return None


def comfort_color(label: str) -> str:
    return {
        "Tight": "#b45309",
        "Comfortable": "#15803d",
        "Relaxed": "#1d4ed8",
        "Long wait": "#7c3aed",
        "Overnight+": "#b91c1c",
    }.get(label, "#475569")


def render_running_day_badges(bits: str):
    badge_cols = st.columns(7)
    if bits:
        for i, day in enumerate(WEEKDAYS):
            runs = bits[i] == "1"
            badge_cols[i].markdown(
                f"<div style='text-align:center; padding:6px; border-radius:6px; "
                f"background:{'#1f7a3d' if runs else '#333'}; color:white; font-size:13px;'>"
                f"{day}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("No running-day data on file — assumed to run daily.")


def render_connection_cards(results: pd.DataFrame, start_label: str, end_label: str, limit: int = 12):
    """Visual journey cards for the top N connections."""
    st.subheader("Journey cards")
    st.caption(f"Showing top {min(limit, len(results))} of {len(results)} — expand any card for booking links.")

    for i, row in results.head(limit).iterrows():
        comfort = row.get("Comfort", "")
        overnight = row.get("Overnight_Layover", "No") == "Yes"
        title = (
            f"#{i + 1}  ·  via {row['Via_Station']} ({row['Via_Code']})  ·  "
            f"{format_duration(row['Total_Hrs'])} total  ·  "
            f"layover {format_duration(row['Layover_Hrs'])}"
        )
        with st.expander(title, expanded=(i < 2)):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Total", format_duration(row["Total_Hrs"]))
            c2.metric("Layover", format_duration(row["Layover_Hrs"]))
            c3.metric("Comfort", comfort)
            c4.metric("Night wait", "Yes" if overnight else "No")

            color = comfort_color(comfort)
            st.markdown(
                f"""
                <div style="border-left:4px solid {color}; padding:12px 16px; margin:8px 0 14px;
                            background:#1a1d24; border-radius:0 8px 8px 0;">
                  <div style="font-size:13px; opacity:0.75; margin-bottom:6px;">
                    {start_label} → <b>{row['Via_Station']}</b> → {end_label}
                  </div>
                  <div style="display:flex; flex-wrap:wrap; gap:18px; font-size:14px;">
                    <div>
                      <div style="opacity:0.65; font-size:11px;">LEG 1 · {row['Train_1_No']}</div>
                      <div><b>{row['Train_1_Name']}</b></div>
                      <div>{row['Leave_Start']} → {row['Arrive_Mid']}
                           <span style="opacity:0.6;">({format_duration(row['Leg1_Hrs'])})</span></div>
                      <div style="opacity:0.7; font-size:12px;">Runs: {row['Train_1_Running_Days']}</div>
                    </div>
                    <div style="opacity:0.45; align-self:center;">⇄</div>
                    <div>
                      <div style="opacity:0.65; font-size:11px;">LEG 2 · {row['Train_2_No']}</div>
                      <div><b>{row['Train_2_Name']}</b></div>
                      <div>{row['Leave_Mid']} → {row['Arrive_End']}
                           <span style="opacity:0.6;">({format_duration(row['Leg2_Hrs'])})</span></div>
                      <div style="opacity:0.7; font-size:12px;">Runs: {row['Train_2_Running_Days']}</div>
                    </div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if int(row.get("Train2_Day_Offset", 0) or 0) > 0:
                st.caption(
                    f"Train 2 is on travel day +{int(row['Train2_Day_Offset'])} "
                    "(layover / first leg crossed midnight)."
                )

            l1, l2 = st.columns(2)
            l1.link_button(
                f"IRCTC · Train {row['Train_1_No']}",
                irctc_train_url(row["Train_1_No"]),
                use_container_width=True,
            )
            l2.link_button(
                f"IRCTC · Train {row['Train_2_No']}",
                irctc_train_url(row["Train_2_No"]),
                use_container_width=True,
            )


@st.cache_data(show_spinner=False)
def cached_via_options(start_code, end_code):
    return get_possible_via_stations(start_code, end_code)


@st.cache_data(show_spinner=False)
def cached_search_trains(query):
    return search_trains(query)


@st.cache_data(show_spinner=False)
def cached_hubs(start_code, end_code):
    return get_top_via_hubs(start_code, end_code)


@st.cache_data(show_spinner=False)
def cached_stats():
    return get_data_stats()


# --- UI SETUP ---
st.set_page_config(page_title="Train Connector", layout="wide", page_icon="🚆")
st.title("🚆 Train Connector")
st.markdown(
    "Find **split-ticket connecting routes**, check **direct trains**, explore stations, "
    "and look up full schedules — when a direct seat isn't available."
)

tab_connect, tab_direct, tab_station, tab_lookup, tab_data = st.tabs(
    [
        "🔎 Find Connections",
        "➡️ Direct Trains",
        "📍 Station Explorer",
        "🚂 Train Lookup",
        "📊 Data",
    ]
)

# =====================================================================================
# TAB 1: FIND CONNECTIONS
# =====================================================================================
with tab_connect:
    if "swap_tick" not in st.session_state:
        st.session_state.swap_tick = 0

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
            # Swap via session keys on next rerun
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

    st.markdown("---")
    st.subheader("Advanced Filters")
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
                smart_via_list = cached_via_options(s_code_live, e_code_live)

            if smart_via_list:
                via_station = st.selectbox(
                    "Select Mid-Station",
                    options=[""] + smart_via_list,
                    index=0,
                    help="Only stations that actually connect this route.",
                )
            else:
                if s_code_live and e_code_live:
                    st.caption("No route-specific mid-stations found yet — showing full list.")
                via_station = st.selectbox(
                    "Select Mid-Station",
                    options=[""] + station_list,
                    index=0,
                )

    with col4:
        use_max_wait = st.checkbox(
            "Set waiting time (layover) range",
            help="Default layover window is 1–12 hours.",
        )
        min_wait, max_wait = 1, 12
        if use_max_wait:
            min_wait, max_wait = st.slider(
                "Layover window (Hours)",
                min_value=0,
                max_value=24,
                value=(1, 8),
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
        use_date = st.checkbox(
            "Filter by travel date",
            help="Only connections where both trains run on the chosen date (and day offset).",
        )
        search_date = None
        if use_date:
            search_date = st.date_input(
                "Date of travel",
                value=datetime.date.today(),
                min_value=datetime.date.today(),
            )
            st.caption(f"Selected day: **{search_date.strftime('%A')}**")

    with col8:
        sort_choice = st.radio(
            "Sort results by",
            options=["⚡ Fastest total journey", "⏱️ Shortest layover", "🕐 Earliest departure"],
            index=0,
        )
        sort_by = {
            "⚡ Fastest total journey": "fastest",
            "⏱️ Shortest layover": "layover",
            "🕐 Earliest departure": "departure",
        }[sort_choice]

    with col9:
        exclude_overnight = st.checkbox(
            "Exclude overnight layovers",
            help="Hide connections that cross midnight or sit through late-night hours.",
        )
        max_results = st.number_input(
            "Max results",
            min_value=10,
            max_value=500,
            value=100,
            step=10,
            help="Caps how many routes are returned after sorting.",
        )
        view_mode = st.radio(
            "Results view",
            options=["Cards + table", "Cards only", "Table only"],
            index=0,
        )

    st.markdown("---")

    if st.button("🔍 Find Connections", type="primary", use_container_width=True):
        if not start_station or not end_station:
            st.error("Please select both a Departure and Arrival station.")
        elif start_station == end_station:
            st.warning("Departure and Arrival stations cannot be the same.")
        else:
            with st.spinner("Searching connecting routes..."):
                s_code = extract_code(start_station)
                e_code = extract_code(end_station)
                v_code = extract_code(via_station) if use_via and via_station else None

                # Soft hint: direct trains exist
                directs = find_direct_trains(s_code, e_code, search_date=search_date)
                if not directs.empty:
                    st.info(
                        f"**{len(directs)} direct train(s)** also run on this corridor — "
                        "see the **Direct Trains** tab if you don't need a connection."
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
                    sort_by=sort_by,
                    exclude_overnight=exclude_overnight,
                    max_results=int(max_results),
                )

            if results.empty:
                st.warning(
                    "No connections found matching your filters. "
                    "Try widening the layover range, allowing overnight waits, "
                    "or clearing time/date filters."
                )
            else:
                st.success(f"Found {len(results)} optimized connection(s)!")

                fastest_time = results["Total_Hrs"].min()
                best_layover = results["Layover_Hrs"].min()
                via_count = results["Via_Code"].nunique()

                m1, m2, m3, m4 = st.columns(4)
                m1.metric("Connections", len(results))
                m2.metric("Fastest", format_duration(fastest_time))
                m3.metric("Shortest wait", format_duration(best_layover))
                m4.metric("Via hubs", via_count)

                if use_date:
                    unknown_count = (
                        (results["Train_1_Running_Days"] == "Unknown")
                        | (results["Train_2_Running_Days"] == "Unknown")
                    ).sum()
                    if unknown_count:
                        st.caption(
                            f"{unknown_count} route(s) include a train with no running-days "
                            "data — assumed to run; verify before booking."
                        )

                # Top interchange hubs for this search
                with st.expander("Top interchange hubs for this route"):
                    hubs = cached_hubs(s_code, e_code)
                    if hubs.empty:
                        st.caption("No hub breakdown available.")
                    else:
                        st.dataframe(hubs, use_container_width=True, hide_index=True)

                st.markdown("---")

                if view_mode in ("Cards + table", "Cards only"):
                    render_connection_cards(results, start_station, end_station)

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
                                "Layover", help="Waiting time at mid-station", format="%.1f"
                            ),
                            "Leg1_Hrs": st.column_config.NumberColumn("Leg 1 (Hrs)", format="%.1f"),
                            "Leg2_Hrs": st.column_config.NumberColumn("Leg 2 (Hrs)", format="%.1f"),
                            "Comfort": st.column_config.TextColumn("Comfort"),
                            "Overnight_Layover": st.column_config.TextColumn("Overnight"),
                            "Train_1_Running_Days": st.column_config.TextColumn("Train 1 Runs On"),
                            "Train_2_Running_Days": st.column_config.TextColumn("Train 2 Runs On"),
                        },
                    )

                csv_bytes = results.to_csv(index=False).encode("utf-8")
                st.download_button(
                    "⬇️ Download results as CSV",
                    data=csv_bytes,
                    file_name=f"connections_{s_code}_{e_code}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

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

    d3, d4 = st.columns(2)
    with d3:
        d_use_date = st.checkbox("Filter by travel date", key="direct_date_on")
        d_date = None
        if d_use_date:
            d_date = st.date_input(
                "Travel date",
                value=datetime.date.today(),
                min_value=datetime.date.today(),
                key="direct_date",
            )
    with d4:
        d_sort = st.radio(
            "Sort by",
            options=["Fastest", "Earliest departure"],
            horizontal=True,
            key="direct_sort",
        )
        d_sort_by = "fastest" if d_sort == "Fastest" else "departure"

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
                    sort_by=d_sort_by,
                )
            if directs.empty:
                st.warning("No direct trains found. Try the Connections tab for split-ticket routes.")
            else:
                st.success(f"{len(directs)} direct train(s) found.")
                m1, m2 = st.columns(2)
                m1.metric("Trains", len(directs))
                m2.metric("Fastest", format_duration(directs["Duration_Hrs"].min()))

                st.dataframe(
                    directs,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Duration_Hrs": st.column_config.ProgressColumn(
                            "Duration (Hrs)",
                            format="%f",
                            min_value=0,
                            max_value=float(directs["Duration_Hrs"].max()),
                        ),
                    },
                )

                st.markdown("**Quick IRCTC links**")
                link_cols = st.columns(min(4, len(directs)))
                for i, row in directs.head(8).iterrows():
                    link_cols[i % len(link_cols)].link_button(
                        f"{row['Train_No']}",
                        irctc_train_url(row["Train_No"]),
                        use_container_width=True,
                    )

                st.download_button(
                    "⬇️ Download as CSV",
                    data=directs.to_csv(index=False).encode("utf-8"),
                    file_name=f"direct_{extract_code(d_start)}_{extract_code(d_end)}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

# =====================================================================================
# TAB 3: STATION EXPLORER
# =====================================================================================
with tab_station:
    st.subheader("Station explorer")
    st.markdown("See every train that stops at a station — useful for planning layovers.")

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
            st.download_button(
                "⬇️ Download as CSV",
                data=trains.to_csv(index=False).encode("utf-8"),
                file_name=f"station_{code}_trains.csv",
                mime="text/csv",
                use_container_width=True,
            )

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
            render_running_day_badges(summary["running_days_bits"])

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

            csv_bytes = schedule.to_csv(index=False).encode("utf-8")
            st.download_button(
                "⬇️ Download schedule as CSV",
                data=csv_bytes,
                file_name=f"schedule_{selected_train_number}.csv",
                mime="text/csv",
                use_container_width=True,
            )

# =====================================================================================
# TAB 5: DATA OVERVIEW
# =====================================================================================
with tab_data:
    st.subheader("Dataset coverage")
    stats = cached_stats()
    a, b, c, d = st.columns(4)
    a.metric("Trains", f"{stats['trains']:,}")
    b.metric("Schedule stops", f"{stats['schedule_rows']:,}")
    c.metric("Stations (schedule)", f"{stats['stations_in_schedule']:,}")
    d.metric("Stations (master)", f"{stats['stations_master']:,}")

    e, f = st.columns(2)
    e.metric("Running-days known", f"{stats['running_days_known']:,}")
    f.metric("Running-days unknown / missing", f"{stats['running_days_unknown']:,}")

    st.markdown(
        """
        **Source files (CSV — no separate SQL DB in this project):**
        - `stations.csv` — station code ↔ name master list
        - `train_schedule_scrapped.csv` — stop-by-stop timings per train
        - `running_days_scrapped.csv` — weekly running pattern (Mon→Sun bitmask)

        Station codes in the schedule file are often blank; the engine fills them
        from `stations.csv` (or invents a stable fallback code so intersections still work).
        """
    )

# --- FOOTER ---
st.markdown("---")
st.info(
    """
**Notes & disclaimer**
- Running-day filtering uses scraped weekly schedules; trains with no data are **assumed to run daily**.
- This app does **not** check seat availability, quota, or dynamic pricing — book via IRCTC.
- Always cross-check timings and running days on the official IRCTC site before booking.
"""
)

st.markdown(
    """
    <div style='text-align: center; color: gray; padding-top: 12px;'>
        <p>Suggestions / features: <b>wazirnoob@gmail.com</b></p>
        <p>© 2026 All Rights Reserved.</p>
    </div>
    """,
    unsafe_allow_html=True,
)
