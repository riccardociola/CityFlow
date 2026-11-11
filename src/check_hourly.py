
"""Check hourly dataset quality and evaluate a persistence baseline."""

from datetime import datetime, timezone
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "heidelberg"

con = duckdb.connect()
con.read_parquet(str(DATA / "hourly_observations.parquet")).create_view("hourly")
con.read_parquet(str(DATA / "hourly_ml.parquet")).create_view("ml")


def percentage(part, total):
    return 100 * part / total if total else 0


def utc_date(timestamp):
    return datetime.fromtimestamp(
        timestamp, tz=timezone.utc
    ).strftime("%Y-%m-%d")


# 1. Hourly coverage per station

print("\n=== STATION COVERAGE ===")

results = con.execute("""
    SELECT station_name,
           COUNT(*) AS total_hours,
           COUNT(*) FILTER (WHERE usable) AS usable_hours,
           ROUND(100.0 * AVG(CAST(usable AS INTEGER)), 1) AS coverage
    FROM hourly
    GROUP BY station_id, station_name
    ORDER BY coverage ASC
    LIMIT 10
""").fetchall()

print("Ten stations with the lowest usable hourly coverage:")

for name, total, valid, coverage in results:
    print(f"  {name}: {coverage}% ({valid:,}/{total:,})")


# 2. Label freshness

print("\n=== LABEL FRESHNESS ===")

total, updated, fresh_hour = con.execute("""
    SELECT
        COUNT(*),
        COUNT(*) FILTER (
            WHERE target.event_time > source.event_time
        ),
        COUNT(*) FILTER (
            WHERE target.state_age_minutes <= 60
        )
    FROM ml AS m
    JOIN hourly AS source
      ON m.station_id = source.station_id
     AND m.issue_time = source.hour_time
    JOIN hourly AS target
      ON m.station_id = target.station_id
     AND m.target_time = target.hour_time
""").fetchone()

print(f"Total one-hour samples: {total:,}")
print(
    f"Station state updated between issue and target: "
    f"{percentage(updated, total):.1f}%"
)
print(
    f"Target state at most 60 minutes old: "
    f"{percentage(fresh_hour, total):.1f}%"
)


# 3. Station shortage distribution

print("\n=== STATION SHORTAGE FREQUENCY ===")

results = con.execute("""
    SELECT station_name,
           COUNT(*) AS n,
           ROUND(100.0 * AVG(shortage_in_1h), 1) AS shortage_pct
    FROM ml
    GROUP BY station_id, station_name
    ORDER BY shortage_pct DESC
    LIMIT 10
""").fetchall()

print("Ten stations with the highest hourly shortage frequency:")

for name, n, shortage in results:
    print(f"  {name}: {shortage}% ({n:,} samples)")


# 4. Chronological train / validation / test boundaries

print("\n=== CHRONOLOGICAL SPLIT ===")

start, end = con.execute("""
    SELECT MIN(issue_time), MAX(target_time)
    FROM ml
""").fetchone()

train_end = start + int(0.70 * (end - start))
val_end = start + int(0.85 * (end - start))

print(f"Training ends:   {utc_date(train_end)}")
print(f"Validation ends: {utc_date(val_end)}")
print(f"Test ends:       {utc_date(end)}")

# Keep samples whose one-hour targets do not cross a split.
train_n, train_rate = con.execute("""
    SELECT COUNT(*), AVG(shortage_in_1h)
    FROM ml
    WHERE target_time <= ?
""", [train_end]).fetchone()

val_n, val_rate = con.execute("""
    SELECT COUNT(*), AVG(shortage_in_1h)
    FROM ml
    WHERE issue_time > ? AND target_time <= ?
""", [train_end, val_end]).fetchone()

test_n = con.execute("""
    SELECT COUNT(*) FROM ml
    WHERE issue_time > ?
""", [val_end]).fetchone()[0]

print(f"Training:   {train_n:,} samples; shortage {train_rate:.1%}")
print(f"Validation: {val_n:,} samples; shortage {val_rate:.1%}")
print(f"Test:       {test_n:,} samples (held out)")


# 5. Persistence baseline on validation data only

print("\n=== PERSISTENCE BASELINE — VALIDATION ===")

tp, fp, fn, tn = con.execute("""
    SELECT
        COUNT(*) FILTER (
            WHERE current_bikes <= 2 AND shortage_in_1h = 1
        ),
        COUNT(*) FILTER (
            WHERE current_bikes <= 2 AND shortage_in_1h = 0
        ),
        COUNT(*) FILTER (
            WHERE current_bikes > 2 AND shortage_in_1h = 1
        ),
        COUNT(*) FILTER (
            WHERE current_bikes > 2 AND shortage_in_1h = 0
        )
    FROM ml
    WHERE issue_time > ? AND target_time <= ?
""", [train_end, val_end]).fetchone()

precision = tp / (tp + fp) if tp + fp else 0
recall = tp / (tp + fn) if tp + fn else 0
f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0
accuracy = (tp + tn) / (tp + fp + fn + tn) if tp + fp + fn + tn else 0

print("Rule: predict shortage if current_bikes <= 2")
print(f"Accuracy:  {accuracy:.3f}")
print(f"Precision: {precision:.3f}")
print(f"Recall:    {recall:.3f}")
print(f"F1 score:  {f1:.3f}")

print(f"\nConfusion matrix:")
print(f"  True positives:  {tp:,}")
print(f"  False positives: {fp:,}")
print(f"  False negatives: {fn:,}")
print(f"  True negatives:  {tn:,}")

con.close()