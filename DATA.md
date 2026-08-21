# Timetable & geo data

## What ships in this repo

| File | Role |
|------|------|
| `stations.csv` | Master station code ↔ name list |
| `train_schedule_scrapped.csv` | Stop-by-stop schedules used by the search engine |
| `running_days_scrapped.csv` | 7-bit Mon..Sun running pattern per train |
| `station_coords.csv` | Lat/lon for maps (from [datameet/railways](https://github.com/datameet/railways) GeoJSON) |

## Live running status / delays

- **Source:** National Train Enquiry System (NTES) — [enquiry.indianrail.gov.in/mntes](https://enquiry.indianrail.gov.in/mntes)
- **Client:** unofficial [`ntes-client`](https://pypi.org/project/ntes-client/) (`from ntes import NTESClient`)
- **Module:** `live_status.py`
- Indian Railways / CRIS do **not** publish an official public developer API. The client can break when NTES changes. Always treat delays as advisory and confirm on the official NTES / RailOne apps.

Layover risk rule (default): if `planned_wait_minutes − arriving_delay_minutes < 20`, the change is flagged **risky** (or **missed** if remaining &lt; 0).

## Maps

- Coordinates: `station_coords.csv` (community datameet dataset, not surveyed for this app)
- Coverage is good for major stations but not 100% of every halt
- UI draws Start → Via → End (or 2-change points) with Streamlit `st.map`

## Refreshing the timetable

### Option A — NTES (documented path used by this project)

```bash
pip install ntes-client
# Refresh specific trains (polite; small batches)
python scripts/refresh_timetable.py --trains 12301,12213,12951

# Refresh a slice of trains already in your schedule file
python scripts/refresh_timetable.py --from-existing --limit 30 --sleep 0.4 --merge

# Dry-run
python scripts/refresh_timetable.py --trains 12301 --dry-run
```

After a successful refresh, **restart** the Streamlit app so `engine.py` reloads the CSVs.

### Option B — Bulk community crawler (railpull)

[shwetankg07/railpull](https://github.com/shwetankg07/railpull) crawls NTES into tidy `stops.csv` + `trains.csv`. Then:

```bash
python scripts/refresh_timetable.py --from-railpull-dir /path/to/railpull/out
```

### Courtesy

NTES is a public enquiry system. Use low concurrency, sleep between calls, and avoid hammering the service. This project is for personal planning, not commercial redistribution of live feeds.
