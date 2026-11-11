"""Audit the station-status data for one CityFlow city.

Run from the CityFlow project root (Windows PowerShell):
    python src/audit_city.py --city bergamo
    python src/audit_city.py --city karlsruhe

Reads data/<city>/stations.parquet and station_status.parquet and writes:
    reports/<city>/station_audit.csv
    reports/<city>/daily_activity.csv
    reports/<city>/summary.txt

Only dependency: duckdb. This is a descriptive audit, NOT a cleaned ML dataset.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re

import duckdb

# Plausible Unix timestamps, used only to avoid nonsensical date calculations.
# Records outside these bounds are counted as invalid, not silently discarded.
TIME_START = 946684800   # 2000-01-01 00:00:00 UTC
TIME_END = 4102444800    # 2100-01-01 00:00:00 UTC


def sql_path(path: Path) -> str:
    """Quote a file path safely as a SQL string literal."""
    return "'" + path.as_posix().replace("'", "''") + "'"


def utc_string(epoch: float | None) -> str:
    if epoch is None:
        return ""
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def write_csv(path: Path, rows: list[dict], columns: list[str] | None = None) -> None:
    if columns is None:
        columns = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def fetch_dicts(con: duckdb.DuckDBPyConnection, query: str) -> list[dict]:
    result = con.execute(query)
    columns = [description[0] for description in result.description]
    return [dict(zip(columns, row)) for row in result.fetchall()]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="bergamo", help="Extracted data folder name")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", args.city):
        parser.error("--city must be a folder name (letters, numbers, _ or -)")

    root = Path(__file__).resolve().parent.parent
    data = root / "data" / args.city.lower()
    output = root / "reports" / args.city.lower()
    stations_file = data / "stations.parquet"
    status_file = data / "station_status.parquet"
    for path in (stations_file, status_file):
        if not path.is_file():
            parser.error(f"Missing {path}. Check the data folder or extraction step.")
    output.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute(
            f"CREATE TEMP VIEW stations AS SELECT * FROM read_parquet({sql_path(stations_file)})"
        )
        con.execute(
            f"CREATE TEMP VIEW status AS SELECT * FROM read_parquet({sql_path(status_file)})"
        )

        required_stations = {"id", "name"}
        required_status = {
            "station_id", "time", "bikes", "booked_bikes",
            "bikes_available_to_rent", "free_racks", "maintenance",
        }
        columns_stations = {r[0] for r in con.execute("DESCRIBE stations").fetchall()}
        columns_status = {r[0] for r in con.execute("DESCRIBE status").fetchall()}
        if missing := required_stations - columns_stations:
            raise ValueError(f"stations.parquet is missing columns: {sorted(missing)}")
        if missing := required_status - columns_status:
            raise ValueError(f"station_status.parquet is missing columns: {sorted(missing)}")

        # Duplicate station IDs are reported below, but deduplicated here to
        # avoid accidentally duplicating status rows during metadata joins.
        con.execute("""
            CREATE TEMP VIEW stations_unique AS
            SELECT id, ANY_VALUE(name) AS name
            FROM stations GROUP BY id
        """)

        # Dataset-level checks, including rows that do not match a known station.
        meta_rows, unique_stations = con.execute(
            "SELECT COUNT(*), COUNT(DISTINCT id) FROM stations"
        ).fetchone()
        global_stats = fetch_dicts(con, f"""
            SELECT
                COUNT(*) AS total_status_records,
                COUNT(DISTINCT station_id) AS station_ids_in_status,
                MIN(time) FILTER (WHERE time BETWEEN {TIME_START} AND {TIME_END}) AS first_epoch,
                MAX(time) FILTER (WHERE time BETWEEN {TIME_START} AND {TIME_END}) AS last_epoch,
                COUNT(*) FILTER (WHERE time IS NULL OR time < {TIME_START}
                                    OR time > {TIME_END}) AS invalid_time_records,
                COUNT(*) FILTER (WHERE bikes_available_to_rent IS NULL) AS null_availability_records,
                COUNT(*) FILTER (WHERE maintenance IS NULL) AS null_maintenance_records,
                COUNT(*) FILTER (WHERE maintenance = TRUE) AS offline_records,
                COUNT(*) FILTER (WHERE bikes < 0 OR booked_bikes < 0
                                    OR bikes_available_to_rent < 0 OR free_racks < 0)
                        AS negative_count_records,
                COUNT(*) FILTER (WHERE bikes_available_to_rent > bikes) AS available_exceeds_bikes_records,
                COUNT(*) FILTER (WHERE maintenance = FALSE AND bikes >= 0
                                    AND bikes_available_to_rent BETWEEN 0 AND bikes) AS valid_online_records,
                COUNT(*) FILTER (WHERE maintenance = FALSE AND bikes >= 0
                                    AND bikes_available_to_rent BETWEEN 0 AND LEAST(2, bikes)) AS shortage_records
            FROM status
        """)[0]
        orphan_records = con.execute("""
            SELECT COUNT(*) FROM status AS t
            LEFT JOIN stations_unique AS s ON t.station_id = s.id
            WHERE s.id IS NULL
        """).fetchone()[0]

        # A gap is elapsed time between two RECORDED CHANGES at one station.
        # It is a review signal, not proof that the source feed was down.
        station_rows = fetch_dicts(con, f"""
            WITH events AS (
                SELECT
                    station_id,
                    COUNT(*) AS status_records,
                    COUNT(time) - COUNT(DISTINCT time) AS duplicate_timestamps,
                    MIN(time) FILTER (WHERE time BETWEEN {TIME_START} AND {TIME_END}) AS first_epoch,
                    MAX(time) FILTER (WHERE time BETWEEN {TIME_START} AND {TIME_END}) AS last_epoch,
                    COUNT(DISTINCT FLOOR(time / 86400.0)) FILTER (
                        WHERE time BETWEEN {TIME_START} AND {TIME_END}
                    ) AS days_with_records,
                    COUNT(*) FILTER (WHERE maintenance = TRUE) AS offline_records,
                    COUNT(*) FILTER (WHERE maintenance IS NULL) AS null_maintenance_records,
                    COUNT(*) FILTER (WHERE bikes_available_to_rent IS NULL) AS null_availability_records,
                    COUNT(*) FILTER (WHERE bikes_available_to_rent < 0) AS negative_availability_records,
                    COUNT(*) FILTER (WHERE maintenance = FALSE AND bikes >= 0
                                        AND bikes_available_to_rent BETWEEN 0 AND bikes) AS valid_online_records,
                    COUNT(*) FILTER (WHERE maintenance = FALSE AND bikes >= 0
                                        AND bikes_available_to_rent BETWEEN 0 AND LEAST(2, bikes)) AS shortage_records,
                    COUNT(DISTINCT CASE WHEN maintenance = FALSE AND bikes >= 0
                                             AND bikes_available_to_rent BETWEEN 0 AND bikes
                                       THEN bikes_available_to_rent END) AS unique_online_availability
                FROM status GROUP BY station_id
            ),
            time_deltas AS (
                SELECT station_id,
                       time - LAG(time) OVER (PARTITION BY station_id ORDER BY time) AS gap_seconds
                FROM status
                WHERE time BETWEEN {TIME_START} AND {TIME_END}
            ),
            gaps AS (
                SELECT station_id,
                       MAX(gap_seconds) / 3600.0 AS max_gap_hours,
                       QUANTILE_CONT(gap_seconds / 3600.0, 0.95) AS p95_gap_hours,
                       COUNT(*) FILTER (WHERE gap_seconds > 86400) AS gaps_over_24h
                FROM time_deltas GROUP BY station_id
            )
            SELECT
                s.id AS station_id, s.name AS station_name,
                COALESCE(e.status_records, 0) AS status_records,
                COALESCE(e.duplicate_timestamps, 0) AS duplicate_timestamps,
                e.first_epoch, e.last_epoch,
                ROUND((e.last_epoch - e.first_epoch) / 86400.0, 1) AS span_days,
                COALESCE(e.days_with_records, 0) AS days_with_records,
                ROUND(g.max_gap_hours, 1) AS max_gap_hours,
                ROUND(g.p95_gap_hours, 1) AS p95_gap_hours,
                COALESCE(g.gaps_over_24h, 0) AS gaps_over_24h,
                COALESCE(e.offline_records, 0) AS offline_records,
                COALESCE(e.null_maintenance_records, 0) AS null_maintenance_records,
                COALESCE(e.null_availability_records, 0) AS null_availability_records,
                COALESCE(e.negative_availability_records, 0) AS negative_availability_records,
                COALESCE(e.valid_online_records, 0) AS valid_online_records,
                COALESCE(e.shortage_records, 0) AS shortage_records,
                COALESCE(e.unique_online_availability, 0) AS unique_online_availability,
                ROUND(100.0 * e.offline_records / NULLIF(e.status_records, 0), 2) AS offline_event_pct,
                ROUND(100.0 * e.shortage_records / NULLIF(e.valid_online_records, 0), 2) AS shortage_event_pct
            FROM stations_unique AS s
            LEFT JOIN events AS e ON s.id = e.station_id
            LEFT JOIN gaps AS g ON s.id = g.station_id
            ORDER BY status_records DESC, station_id
        """)

        for row in station_rows:
            row["first_utc"] = utc_string(row.pop("first_epoch"))
            row["last_utc"] = utc_string(row.pop("last_epoch"))
            flags = []
            if row["status_records"] == 0:
                flags.append("NO_STATUS")
            else:
                if row["span_days"] is not None and row["span_days"] < 30:
                    flags.append("SHORT_HISTORY")
                if row["status_records"] < 100:
                    flags.append("FEW_EVENTS")
                if row["max_gap_hours"] is not None and row["max_gap_hours"] > 24:
                    flags.append("GAP_OVER_24H")
                if row["unique_online_availability"] <= 1:
                    flags.append("NO_ONLINE_VARIATION")
                if row["duplicate_timestamps"]:
                    flags.append("DUPLICATE_TIME")
                if row["valid_online_records"] == 0:
                    flags.append("NO_VALID_ONLINE_RECORDS")
            row["review_flags"] = ";".join(flags)

        station_report = output / "station_audit.csv"
        write_csv(station_report, station_rows)

        # City-wide calendar, including days with zero event records. These
        # rows help identify probable system-wide outages or quiet periods.
        daily_counts = con.execute(f"""
            SELECT CAST(FLOOR(time / 86400.0) AS BIGINT) AS day_number,
                   COUNT(*) AS status_records,
                   COUNT(DISTINCT station_id) AS stations_with_records
            FROM status
            WHERE time BETWEEN {TIME_START} AND {TIME_END}
            GROUP BY 1 ORDER BY 1
        """).fetchall()
        count_by_day = {int(day): (int(count), int(nstations))
                        for day, count, nstations in daily_counts}
        daily_rows = []
        if count_by_day:
            for day in range(min(count_by_day), max(count_by_day) + 1):
                count, nstations = count_by_day.get(day, (0, 0))
                date = (datetime(1970, 1, 1, tzinfo=timezone.utc)
                        + timedelta(days=day)).strftime("%Y-%m-%d")
                daily_rows.append({
                    "date_utc": date,
                    "status_records": count,
                    "stations_with_records": nstations,
                })
        daily_report = output / "daily_activity.csv"
        write_csv(daily_report, daily_rows,
                  ["date_utc", "status_records", "stations_with_records"])

        n_observed = sum(r["status_records"] > 0 for r in station_rows)
        n_zero_event_days = sum(r["status_records"] == 0 for r in daily_rows)
        valid_online = global_stats["valid_online_records"]
        shortage_share = (
            100 * global_stats["shortage_records"] / valid_online
            if valid_online else None
        )
        offline_share = (
            100 * global_stats["offline_records"] / global_stats["total_status_records"]
            if global_stats["total_status_records"] else None
        )

        def fmt_pct(value: float | None) -> str:
            return "N/A" if value is None else f"{value:.2f}%"

        lines = [
            f"CityFlow data-quality audit: {args.city}",
            "=" * 50,
            f"Metadata stations (rows / unique IDs): {meta_rows:,} / {unique_stations:,}",
            f"Stations with at least one status record: {n_observed:,}",
            f"Stations with no status records: {unique_stations - n_observed:,}",
            f"Status records: {global_stats['total_status_records']:,}",
            f"Orphan status records (missing station metadata): {orphan_records:,}",
            f"First valid timestamp: {utc_string(global_stats['first_epoch']) or 'N/A'}",
            f"Last valid timestamp:  {utc_string(global_stats['last_epoch']) or 'N/A'}",
            f"UTC days in range / days with zero recorded events: {len(daily_rows):,} / {n_zero_event_days:,}",
            f"Invalid timestamps: {global_stats['invalid_time_records']:,}",
            f"Missing availability: {global_stats['null_availability_records']:,}",
            f"Missing maintenance flag: {global_stats['null_maintenance_records']:,}",
            f"Negative bike/dock count records: {global_stats['negative_count_records']:,}",
            f"Available-to-rent > docked bikes: {global_stats['available_exceeds_bikes_records']:,}",
            f"Offline status records: {global_stats['offline_records']:,} ({fmt_pct(offline_share)} of records)",
            f"Valid online status records: {valid_online:,}",
            f"Shortage (0-2 available bikes) records: {global_stats['shortage_records']:,}",
            f"Shortage among valid online records: {fmt_pct(shortage_share)}",
            "",
            "INTERPRETATION NOTES:",
            "- Percentages describe RECORDS, not the fraction of time in each state.",
            "- Per-station gaps are intervals between recorded statuses; not proven outages.",
            "- Flags use exploratory cutoffs (<30 d history, <100 records, >24 h gap).",
            "- Do not train on event rows directly. Build a fresh, hourly as-of dataset first.",
        ]
        summary_report = output / "summary.txt"
        summary_report.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("\n".join(lines))

        longest = sorted(
            (r for r in station_rows if r["max_gap_hours"] is not None),
            key=lambda r: r["max_gap_hours"], reverse=True
        )[:5]
        if longest:
            print("\nTop 5 longest gaps between recorded station statuses:")
            for row in longest:
                print(f"  {row['station_id']}: {row['station_name']} — "
                      f"{row['max_gap_hours']:.1f} hours; "
                      f"{row['status_records']:,} records")
        print(f"\nReports saved in: {output}")
        print("  - station_audit.csv (one row per station)")
        print("  - daily_activity.csv (one row per UTC calendar day)")
        print("  - summary.txt (overview to share for interpretation)")
    finally:
        con.close()


if __name__ == "__main__":
    main()
