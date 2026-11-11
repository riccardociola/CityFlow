
from pathlib import Path
import duckdb

# --------------------------------------------------
# Configuration
# --------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = PROJECT_ROOT / "data" / "raw"
OUTPUT_DIR = PROJECT_ROOT / "data"

# Add or remove cities here
CITIES = [
    "Bergamo",
    "Karlsruhe",
    "Heidelberg",
]

# Set False if you only want station availability
INCLUDE_TRIPS = True


def sql_path(path: Path) -> str:
    """Convert a filesystem path into a SQL string literal."""
    path_str = path.resolve().as_posix()
    return "'" + path_str.replace("'", "''") + "'"


def export_parquet(con, query: str, path: Path):
    """Save the results of a SQL query as Parquet."""
    con.execute(
        f"COPY ({query}) TO {sql_path(path)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD)"
    )


def main():
    # Check that required files exist
    required_files = ["cities.csv", "stations.csv", "station_status.csv"]

    if INCLUDE_TRIPS:
        required_files.append("trips.csv")

    for filename in required_files:
        if not (RAW_DIR / filename).exists():
            raise FileNotFoundError(
                f"Missing {filename} in {RAW_DIR}"
            )

    con = duckdb.connect()

    # These are small tables: load them once
    con.execute(f"""
        CREATE TEMP TABLE cities AS
        SELECT * FROM read_csv_auto(
            {sql_path(RAW_DIR / "cities.csv")}
        )
    """)

    con.execute(f"""
        CREATE TEMP TABLE stations AS
        SELECT * FROM read_csv_auto(
            {sql_path(RAW_DIR / "stations.csv")}
        )
    """)

    # --------------------------------------------------
    # Extract each city
    # --------------------------------------------------

    for city_name in CITIES:

        # Find city by name
        result = con.execute(
            """
            SELECT id, name
            FROM cities
            WHERE lower(trim(name)) = lower(trim(?))
            """,
            [city_name],
        ).fetchone()

        if result is None:
            print(f"City not found: {city_name}")
            continue

        city_id, official_name = result

        folder = OUTPUT_DIR / city_name.lower().replace(" ", "_")
        folder.mkdir(parents=True, exist_ok=True)

        print(f"\nExtracting {official_name} (ID: {city_id})")

        # 1. City metadata
        export_parquet(
            con,
            f"SELECT * FROM cities WHERE id = {city_id}",
            folder / "cities.parquet",
        )

        # 2. Stations belonging to this city
        export_parquet(
            con,
            f"SELECT * FROM stations WHERE city_id = {city_id}",
            folder / "stations.parquet",
        )

        # 3. Station availability history
        export_parquet(
            con,
            f"""
            SELECT status.*
            FROM read_csv_auto(
                {sql_path(RAW_DIR / "station_status.csv")}
            ) AS status
            JOIN stations AS s
                ON status.station_id = s.id
            WHERE s.city_id = {city_id}
            """,
            folder / "station_status.parquet",
        )

        # 4. Trips belonging to this city
        if INCLUDE_TRIPS:
            export_parquet(
                con,
                f"""
                SELECT *
                FROM read_csv_auto(
                    {sql_path(RAW_DIR / "trips.csv")}
                )
                WHERE city_id = {city_id}
                """,
                folder / "trips.parquet",
            )

        print(f"Saved data to: {folder}")

    con.close()
    print("\nExtraction complete!")


if __name__ == "__main__":
    main()