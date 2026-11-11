
"""Build hourly station states and one-hour shortage targets."""

from pathlib import Path
import argparse
import duckdb


def sql_path(path):
    return "'" + path.as_posix().replace("'", "''") + "'"


parser = argparse.ArgumentParser()
parser.add_argument("--city", default="heidelberg")
parser.add_argument("--station-age", type=int, default=6)
parser.add_argument("--feed-age", type=int, default=2)
args = parser.parse_args()

if not args.city.replace("_", "").replace("-", "").isalnum():
    parser.error("Invalid city folder name")
if min(args.station_age, args.feed_age) < 1:
    parser.error("Age limits must be positive")

folder = Path(__file__).resolve().parent.parent / "data" / args.city.lower()
stations = folder / "stations.parquet"
statuses = folder / "station_status.parquet"

for path in (stations, statuses):
    if not path.exists():
        parser.error(f"File not found: {path}")

con = duckdb.connect()

# 1. Load status events. Keep maintenance and invalid state records:
# they must not be silently replaced with an earlier valid state.

con.execute(f"""
    CREATE TEMP TABLE events AS
    SELECT station_id, CAST(time AS BIGINT) AS event_time, bikes,
           booked_bikes, bikes_available_to_rent, free_racks, maintenance
    FROM read_parquet({sql_path(statuses)})
    WHERE time BETWEEN 946684800 AND 4102444800
""")

# 2. Keep stations with at least one valid online observation.

con.execute(f"""
    CREATE TEMP TABLE stations AS
    SELECT id AS station_id, name AS station_name
    FROM read_parquet({sql_path(stations)})
    WHERE id IN (
        SELECT DISTINCT station_id FROM events
        WHERE maintenance = FALSE AND bikes >= 0
          AND bikes_available_to_rent BETWEEN 0 AND bikes
    )
""")

# 3. Construct a grid of UTC hourly timestamps.

first, last = con.execute(
    "SELECT MIN(event_time), MAX(event_time) FROM events"
).fetchone()

start = ((first + 3599) // 3600) * 3600
stop = (last // 3600) * 3600 + 1

# 4. Reconstruct station states using as-of joins.
# Both joins only use events at or BEFORE the observation time.

con.execute(f"""
    CREATE TEMP TABLE hourly AS
    WITH grid AS (
        SELECT s.*, t.hour_time
        FROM stations s
        CROSS JOIN range({start}, {stop}, 3600) t(hour_time)
    ),
    latest_station AS (
        SELECT g.*, e.event_time, e.bikes, e.booked_bikes,
               e.bikes_available_to_rent, e.free_racks, e.maintenance
        FROM grid g
        ASOF LEFT JOIN events e
          ON g.station_id = e.station_id
         AND g.hour_time >= e.event_time
    ),
    latest_feed AS (
        SELECT s.*, f.event_time AS feed_time
        FROM latest_station s
        ASOF LEFT JOIN (
            SELECT DISTINCT event_time FROM events
        ) f ON s.hour_time >= f.event_time
    ),
    quality AS (
        SELECT *,
            CASE
                WHEN hour_time - event_time
                         BETWEEN 0 AND {args.station_age * 3600}
                 AND hour_time - feed_time
                         BETWEEN 0 AND {args.feed_age * 3600}
                 AND maintenance = FALSE
                 AND bikes >= 0
                 AND bikes_available_to_rent BETWEEN 0 AND bikes
                 AND COALESCE(booked_bikes, 0) >= 0
                 AND COALESCE(free_racks, 0) >= 0
                THEN TRUE ELSE FALSE
            END AS usable
        FROM latest_feed
    )
    SELECT station_id, station_name, hour_time,
           event_time, feed_time, usable,
           CASE WHEN usable THEN bikes_available_to_rent END
               AS available_bikes,
           ROUND((hour_time - event_time) / 60.0, 1)
               AS state_age_minutes
    FROM quality
""")

# 5. Construct a one-hour-ahead target.
# Both current and future states must be usable.
# Future values are labels, never input features.

con.execute("""
    CREATE TEMP TABLE ml AS
    SELECT a.station_id, a.station_name,
           a.hour_time AS issue_time,
           b.hour_time AS target_time,
           a.available_bikes AS current_bikes,
           a.state_age_minutes,
           b.available_bikes AS bikes_in_1h,
           CAST(b.available_bikes <= 2 AS INTEGER) AS shortage_in_1h
    FROM hourly a
    JOIN hourly b
      ON a.station_id = b.station_id
     AND b.hour_time = a.hour_time + 3600
    WHERE a.usable AND b.usable
""")

# 6. Export the reconstructed dataset and ML samples.

for table, filename in [
    ("hourly", "hourly_observations.parquet"),
    ("ml", "hourly_ml.parquet")
]:
    output = folder / filename
    con.execute(
        f"COPY {table} TO {sql_path(output)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )
    print("Saved:", output)

# 7. Report the actual usable coverage and label frequency.

n_total, n_good = con.execute("""
    SELECT COUNT(*), COUNT(*) FILTER (WHERE usable)
    FROM hourly
""").fetchone()

n_ml, n_shortage, n_stations = con.execute("""
    SELECT COUNT(*), SUM(shortage_in_1h),
           COUNT(DISTINCT station_id)
    FROM ml
""").fetchone()

print(
    f"Usable hourly states: {n_good:,}/{n_total:,} "
    f"({n_good / n_total:.1%})"
)
print(f"One-hour samples: {n_ml:,} across {n_stations} stations")

if n_ml:
    print(f"Hourly shortage-label frequency: {n_shortage / n_ml:.1%}")

print("All timestamps are Unix seconds in UTC.")
print("Freshness thresholds are provisional.")

con.close()