
"""Compare CityFlow station audit outputs (standard library only).

From the CityFlow project root:
    python src/compare_audits.py bergamo heidelberg

Expected inputs, created by audit_city.py:
    reports/<city>/station_audit.csv
    reports/<city>/daily_activity.csv

This is an event-activity comparison. Days without recorded changes at a
station are NOT proof of a station or feed outage, and event-level shortage
fractions are NOT time-weighted shortage rates.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from statistics import median


def load_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise FileNotFoundError(
            f"Missing {path}. Run audit_city.py for this city first."
        )

    with path.open(newline="", encoding="utf-8-sig") as stream:
        return list(csv.DictReader(stream))


def number(row: dict[str, str], key: str) -> float:
    raw = row.get(key, "")
    return float(raw) if raw not in (None, "") else 0.0


def pct(numerator: int, denominator: int) -> str:
    return (
        f"{100 * numerator / denominator:.1f}%"
        if denominator else "N/A"
    )


def format_date_ranges(days: list[str]) -> str:
    """Print consecutive UTC dates as ranges."""
    if not days:
        return "(none)"

    dates = sorted(date.fromisoformat(day) for day in set(days))
    ranges = []
    start = previous = dates[0]

    for current in dates[1:]:
        if current == previous + timedelta(days=1):
            previous = current
        else:
            ranges.append((start, previous))
            start = previous = current

    ranges.append((start, previous))

    return ", ".join(
        first.isoformat()
        if first == last
        else f"{first.isoformat()} to {last.isoformat()}"
        for first, last in ranges
    )


def describe(city: str, root: Path) -> set[str]:
    report_dir = root / "reports" / city

    stations = load_csv(report_dir / "station_audit.csv")
    daily = load_csv(report_dir / "daily_activity.csv")

    nstations = len(stations)

    nrecords = sum(
        int(number(row, "status_records"))
        for row in stations
    )

    active_days = [
        int(number(row, "days_with_records"))
        for row in stations
    ]

    station_spans = [
        number(row, "span_days")
        for row in stations
    ]

    max_gaps = [
        number(row, "max_gap_hours")
        for row in stations
    ]

    no_variation = [
        row for row in stations
        if number(row, "unique_online_availability") <= 1
    ]

    short_histories = [
        row for row in stations
        if number(row, "span_days") < 30
    ]

    few_events = [
        row for row in stations
        if number(row, "status_records") < 100
    ]

    long_gaps = [
        row for row in stations
        if number(row, "max_gap_hours") > 24
    ]

    very_long_gaps = [
        row for row in stations
        if number(row, "max_gap_hours") > 168
    ]

    duplicate = [
        row for row in stations
        if number(row, "duplicate_timestamps") > 0
    ]

    global_zero_days = {
        row["date_utc"]
        for row in daily
        if int(number(row, "status_records")) == 0
    }

    station_counts_by_day = [
        int(number(row, "stations_with_records"))
        for row in daily
        if int(number(row, "status_records")) > 0
    ]

    daily_counts = [
        int(number(row, "status_records"))
        for row in daily
    ]

    offline = sum(
        int(number(row, "offline_records"))
        for row in stations
    )

    shortages = sum(
        int(number(row, "shortage_records"))
        for row in stations
    )

    valid_online = sum(
        int(number(row, "valid_online_records"))
        for row in stations
    )

    flags = Counter(
        flag
        for row in stations
        for flag in row.get("review_flags", "").split(";")
        if flag
    )

    print(f"\n{'=' * 64}")
    print(f"{city.upper()} — event-data quality indicators")
    print("=" * 64)

    print(f"Stations: {nstations:,}; status records: {nrecords:,}")

    print(
        f"Median records per station: "
        f"{median(int(number(r, 'status_records')) for r in stations):,.0f}"
    )

    print(
        f"Median days with at least one station event: "
        f"{median(active_days):.0f} / {len(daily)}"
    )

    print(
        f"Stations with events on >= 180 calendar days: "
        f"{sum(d >= 180 for d in active_days)} / {nstations}"
    )

    print(
        f"Median first-to-last station span: "
        f"{median(station_spans):.1f} days"
    )

    print(
        f"Median of each station's longest event gap: "
        f"{median(max_gaps):.1f} hours"
    )

    print(
        f"Stations with > 24-hour event gap: "
        f"{len(long_gaps)} / {nstations}"
    )

    print(
        f"Stations with > 7-day event gap: "
        f"{len(very_long_gaps)} / {nstations}"
    )

    print(
        f"Short histories (<30 days): "
        f"{len(short_histories)} / {nstations}"
    )

    print(
        f"Low event counts (<100): "
        f"{len(few_events)} / {nstations}"
    )

    print(
        f"No variation in valid online availability: "
        f"{len(no_variation)} / {nstations}"
    )

    print(
        f"Stations with duplicate station/timestamp pairs: "
        f"{len(duplicate)} / {nstations}"
    )

    print(
        f"Maintenance events: {offline:,} / {nrecords:,} "
        f"({pct(offline, nrecords)})"
    )

    print(
        f"Shortage events among valid online records: "
        f"{shortages:,} / {valid_online:,} "
        f"({pct(shortages, valid_online)})"
    )

    print("\nCity-wide daily activity:")

    print(
        f"  Days with no recorded event: "
        f"{len(global_zero_days)} / {len(daily)}"
    )

    print(
        f"  Zero-event UTC dates: "
        f"{format_date_ranges(sorted(global_zero_days))}"
    )

    print(
        f"  Median records per UTC day (including zeros): "
        f"{median(daily_counts):,.0f}"
    )

    if station_counts_by_day:
        print(
            f"  Median stations reporting changes on a nonzero day: "
            f"{median(station_counts_by_day):.0f} / {nstations}"
        )

    print(
        "  Note: lack of a status change does not prove "
        "that a station was offline."
    )

    if flags:
        print("\nReview flags (number of stations):")

        for flag, count in flags.most_common():
            print(f"  {flag}: {count}")

    print("\nFive stations with the fewest days recording changes:")

    least_active = sorted(
        stations,
        key=lambda r: (
            number(r, "days_with_records"),
            number(r, "status_records")
        )
    )[:5]

    for row in least_active:
        print(
            f"  {row['station_id']} — {row['station_name']}: "
            f"{int(number(row, 'days_with_records'))} active event days, "
            f"{int(number(row, 'status_records')):,} records, "
            f"{number(row, 'span_days'):.1f}-day span"
        )

    return global_zero_days


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "cities",
        nargs="+",
        help="City report folder names, e.g. bergamo heidelberg"
    )

    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent

    zero_by_city = {}

    for city in args.cities:
        city = city.lower()
        zero_by_city[city] = describe(city, root)

    if len(zero_by_city) >= 2:
        shared = set.intersection(*zero_by_city.values())

        print(f"\n{'=' * 64}")
        print("SHARED ZERO-EVENT DATES (all selected cities)")
        print("=" * 64)

        print(
            f"{len(shared)} shared UTC days: "
            f"{format_date_ranges(sorted(shared))}"
        )

        for city, days in zero_by_city.items():
            unique = days - shared

            print(
                f"{city}: {len(unique)} other zero-event days — "
                f"{format_date_ranges(sorted(unique))}"
            )

        print(
            "Matching dates would suggest a shared collection gap, "
            "not prove one."
        )


if __name__ == "__main__":
    main()