
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "heidelberg"

con = duckdb.connect()

# Read the filtered data
stations = con.read_parquet(
    str(DATA / "stations.parquet")
)

status = con.read_parquet(
    str(DATA / "station_status.parquet")
)

# Number of stations
print("Stations:", stations.count("*").fetchall()[0][0])

# Number of historical status records
print("Status records:", status.count("*").fetchall()[0][0])

# Inspect a few records
print("\nStation information:")
print(stations.limit(5).df().to_string(index=False))

# Find the period covered by the dataset
time_range = con.execute(
    """
    SELECT
        MIN(to_timestamp(time)) AS first_observation,
        MAX(to_timestamp(time)) AS last_observation
    FROM read_parquet(?)
    """,
    [str(DATA / "station_status.parquet")],
).fetchone()

print("\nAvailable history:")
print("First observation:", time_range[0])
print("Last observation:", time_range[1])
