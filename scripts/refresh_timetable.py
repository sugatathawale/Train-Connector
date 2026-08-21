#!/usr/bin/env python3
"""
Refresh local timetable CSVs from NTES (National Train Enquiry System).

Source (documented):
  - Portal: https://enquiry.indianrail.gov.in/mntes
  - Client: unofficial `ntes` package (pip install ntes-client)
  - There is NO official public developer API from Indian Railways / CRIS.
    This script is best-effort and may break if NTES changes.

What it writes (same shapes the app already expects):
  - train_schedule_scrapped.csv
  - running_days_scrapped.csv

Usage examples:
  # Refresh a few trains (safe / polite)
  python scripts/refresh_timetable.py --trains 12301,12213,12951

  # Refresh every train already present in the current schedule file
  python scripts/refresh_timetable.py --from-existing --limit 50 --sleep 0.4

  # Dry-run (fetch + print, do not overwrite)
  python scripts/refresh_timetable.py --trains 12301 --dry-run

Optional alternative (bulk community crawler):
  https://github.com/shwetankg07/railpull  — export stops.csv / trains.csv,
  then convert with --from-railpull-dir PATH
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULE_OUT = ROOT / "train_schedule_scrapped.csv"
RUNNING_OUT = ROOT / "running_days_scrapped.csv"

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def days_of_run_to_bits(days_text: str) -> str:
    """
    NTES: 'Mon,tue,wed,thu,fri,sat' or 'Daily' -> '1111110' (Mon..Sun).
    """
    s = (days_text or "").strip()
    if not s:
        return ""
    up = s.upper().replace(".", "")
    if "DAILY" in up or up in ("ALL DAYS", "EVERYDAY"):
        return "1111111"
    bits = ["0"] * 7
    # normalize tokens
    tokens = re.split(r"[,/|\s]+", up)
    aliases = {
        "MON": 0,
        "MONDAY": 0,
        "TUE": 1,
        "TUES": 1,
        "TUESDAY": 1,
        "WED": 2,
        "WEDNESDAY": 2,
        "THU": 3,
        "THUR": 3,
        "THURS": 3,
        "THURSDAY": 3,
        "FRI": 4,
        "FRIDAY": 4,
        "SAT": 5,
        "SATURDAY": 5,
        "SUN": 6,
        "SUNDAY": 6,
    }
    for tok in tokens:
        tok = tok.strip()
        if tok in aliases:
            bits[aliases[tok]] = "1"
    return "".join(bits)


def _norm_time(t: str) -> str:
    t = (t or "").strip()
    if not t or t.upper() in ("SOURCE", "DESTINATION", "--", "-"):
        return "00:00:00"
    parts = t.replace(".", ":").split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return f"{h:02d}:{m:02d}:00"
    except (ValueError, TypeError):
        return "00:00:00"


def fetch_train_from_ntes(train_no: str) -> dict:
    from ntes import NTESClient

    client = NTESClient(timeout=30, retries=2)
    payload = client.schedule(str(train_no).strip())
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected schedule payload for {train_no}")
    return payload


def ntes_payload_to_rows(payload: dict) -> tuple[list[dict], dict]:
    train_no = str(payload.get("TrainNumber") or "").strip()
    train_name = str(payload.get("TrainName") or "").strip()
    source = str(payload.get("SourceName") or payload.get("Source") or "").strip()
    dest = str(payload.get("DestinationName") or payload.get("Destination") or "").strip()
    days = days_of_run_to_bits(str(payload.get("DaysOfRun") or ""))

    schedule_rows = []
    for stn in payload.get("stations") or []:
        if not isinstance(stn, dict):
            continue
        code = str(stn.get("StationCode") or "").strip().upper()
        name = str(stn.get("StationName") or "").strip()
        day = int(stn.get("Day") or 1)
        arr = _norm_time(str(stn.get("STA") or ""))
        dep = _norm_time(str(stn.get("STD") or ""))
        # Origin often has empty STA; destination empty STD — already normalized
        schedule_rows.append(
            {
                "train_number": train_no,
                "train_name": train_name,
                "day": str(day),
                "station_name": name,
                "station_code": code,
                "arrival": arr,
                "departure": dep,
            }
        )

    running = {
        "train_number": train_no,
        "train_name": train_name,
        "source": source,
        "destination": dest,
        "running_days": days,
    }
    return schedule_rows, running


def load_existing_train_numbers(limit: int | None = None) -> list[str]:
    if not SCHEDULE_OUT.exists():
        return []
    nums = []
    seen = set()
    with SCHEDULE_OUT.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            tn = str(row.get("train_number") or "").strip()
            if tn and tn not in seen:
                seen.add(tn)
                nums.append(tn)
                if limit and len(nums) >= limit:
                    break
    return nums


def write_csvs(schedule_rows: list[dict], running_rows: list[dict], dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write {len(schedule_rows)} schedule rows, {len(running_rows)} running-day rows")
        if schedule_rows:
            print(" sample schedule:", schedule_rows[0])
        if running_rows:
            print(" sample running:", running_rows[0])
        return

    with SCHEDULE_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "train_number",
                "train_name",
                "day",
                "station_name",
                "station_code",
                "arrival",
                "departure",
            ],
        )
        w.writeheader()
        w.writerows(schedule_rows)

    with RUNNING_OUT.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=["train_number", "train_name", "source", "destination", "running_days"],
        )
        w.writeheader()
        w.writerows(running_rows)

    print(f"Wrote {SCHEDULE_OUT.name} ({len(schedule_rows)} rows)")
    print(f"Wrote {RUNNING_OUT.name} ({len(running_rows)} rows)")


def import_railpull(dir_path: Path, dry_run: bool) -> None:
    """
    Convert railpull export (stops.csv + trains.csv) into app CSV shapes.
    See https://github.com/shwetankg07/railpull
    """
    stops = dir_path / "stops.csv"
    trains = dir_path / "trains.csv"
    if not stops.exists() or not trains.exists():
        raise SystemExit(f"Expected {stops} and {trains}")

    train_meta = {}
    with trains.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tn = str(row.get("number") or row.get("train_number") or "").strip()
            if not tn:
                continue
            runs = row.get("runs_days") or row.get("running_days") or ""
            # railpull may already use Mon,Wed or Daily
            bits = days_of_run_to_bits(str(runs))
            if re.fullmatch(r"[01]{7}", str(runs).strip()):
                bits = str(runs).strip()
            train_meta[tn] = {
                "train_number": tn,
                "train_name": str(row.get("name") or row.get("train_name") or "").strip(),
                "source": str(row.get("source") or "").strip(),
                "destination": str(row.get("destination") or "").strip(),
                "running_days": bits,
            }

    schedule_rows = []
    with stops.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            tn = str(row.get("train") or row.get("train_number") or "").strip()
            if not tn:
                continue
            meta = train_meta.get(tn, {"train_name": ""})
            day = str(row.get("day") or row.get("day_offset") or "1")
            # day_offset 0 -> day 1
            try:
                if "day_offset" in row and row.get("day_offset") not in (None, ""):
                    day = str(int(row["day_offset"]) + 1)
            except ValueError:
                pass
            schedule_rows.append(
                {
                    "train_number": tn,
                    "train_name": meta.get("train_name") or str(row.get("train_name") or ""),
                    "day": day,
                    "station_name": str(row.get("station_name") or row.get("name") or "").strip(),
                    "station_code": str(row.get("station_code") or row.get("code") or "").strip().upper(),
                    "arrival": _norm_time(str(row.get("arrival") or "")),
                    "departure": _norm_time(str(row.get("departure") or "")),
                }
            )

    running_rows = list(train_meta.values())
    write_csvs(schedule_rows, running_rows, dry_run=dry_run)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh timetable CSVs from NTES or railpull export")
    parser.add_argument("--trains", help="Comma-separated train numbers to refresh")
    parser.add_argument("--from-existing", action="store_true", help="Use train numbers already in schedule CSV")
    parser.add_argument("--limit", type=int, default=0, help="Max trains when using --from-existing")
    parser.add_argument("--sleep", type=float, default=0.35, help="Pause between NTES calls (be polite)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--merge", action="store_true", help="Merge into existing CSVs instead of replace")
    parser.add_argument("--from-railpull-dir", type=str, help="Import railpull stops.csv + trains.csv from DIR")
    args = parser.parse_args()

    if args.from_railpull_dir:
        import_railpull(Path(args.from_railpull_dir), dry_run=args.dry_run)
        return

    train_nos: list[str] = []
    if args.trains:
        train_nos = [t.strip() for t in args.trains.split(",") if t.strip()]
    elif args.from_existing:
        train_nos = load_existing_train_numbers(limit=args.limit or None)
    else:
        parser.print_help()
        print("\nProvide --trains, --from-existing, or --from-railpull-dir", file=sys.stderr)
        sys.exit(2)

    if not train_nos:
        raise SystemExit("No train numbers to refresh")

    print(f"Refreshing {len(train_nos)} train(s) from NTES…")
    all_sched: list[dict] = []
    all_run: list[dict] = []
    errors = 0

    if args.merge and SCHEDULE_OUT.exists() and not args.dry_run:
        # Keep trains not being refreshed
        keep = set(train_nos)
        with SCHEDULE_OUT.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("train_number") or "").strip() not in keep:
                    all_sched.append(row)
        if RUNNING_OUT.exists():
            with RUNNING_OUT.open(newline="", encoding="utf-8") as fh:
                for row in csv.DictReader(fh):
                    if str(row.get("train_number") or "").strip() not in keep:
                        all_run.append(row)

    for i, tn in enumerate(train_nos, start=1):
        try:
            payload = fetch_train_from_ntes(tn)
            sched_rows, running = ntes_payload_to_rows(payload)
            if not sched_rows:
                raise RuntimeError("no stations in payload")
            all_sched.extend(sched_rows)
            all_run.append(running)
            print(f"[{i}/{len(train_nos)}] {tn} OK ({len(sched_rows)} stops, days={running['running_days']})")
        except Exception as exc:  # noqa: BLE001
            errors += 1
            print(f"[{i}/{len(train_nos)}] {tn} FAIL: {exc}", file=sys.stderr)
        time.sleep(max(0.0, args.sleep))

    if not all_sched:
        raise SystemExit("Nothing fetched — not writing files")

    # Deduplicate running by train_number (last wins)
    run_map = {r["train_number"]: r for r in all_run if r.get("train_number")}
    write_csvs(all_sched, list(run_map.values()), dry_run=args.dry_run)
    if errors:
        print(f"Completed with {errors} error(s).", file=sys.stderr)


if __name__ == "__main__":
    main()
