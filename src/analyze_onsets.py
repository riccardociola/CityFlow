
"""Analyze shortage transitions without touching the test set.

Run from the CityFlow root:
    python src/analyze_onsets.py
"""

from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "heidelberg"

con = duckdb.connect()
con.read_parquet(
    str(DATA / "hourly_ml.parquet")
).create_view("ml")
con.read_parquet(
    str(DATA / "hourly_observations.parquet")
).create_view("hourly")

start, end = con.execute(
    "SELECT MIN(issue_time), MAX(target_time) FROM ml"
).fetchone()

train_end = start + int(0.70 * (end - start))
val_end = start + int(0.85 * (end - start))

# Use training data to estimate shortage-onset probabilities.
training = con.execute("""
    SELECT current_bikes, shortage_in_1h
    FROM ml
    WHERE target_time <= ?
      AND current_bikes > 2
""", [train_end]).fetchall()

# Validation data: compare station state at issue and target.
validation = con.execute("""
    SELECT m.current_bikes,
           m.shortage_in_1h,
           (target.event_time > source.event_time) AS updated
    FROM ml m
    JOIN hourly source
      ON m.station_id = source.station_id
     AND m.issue_time = source.hour_time
    JOIN hourly target
      ON m.station_id = target.station_id
     AND m.target_time = target.hour_time
    WHERE m.issue_time > ?
      AND m.target_time <= ?
""", [train_end, val_end]).fetchall()


def bike_group(bikes):
    if bikes <= 5:
        return str(int(bikes))
    if bikes <= 8:
        return "6-8"
    return "9+"


def pct(n, total):
    return 100 * n / total if total else 0


# ---------------------------------------------------------
# 1. Shortage transitions
# ---------------------------------------------------------

print("\n=== SHORTAGE TRANSITIONS ===")

at_risk = sum(1 for bikes, _, _ in validation if bikes > 2)
new_shortages = sum(
    int(y) for bikes, y, _ in validation if bikes > 2
)

already_short = len(validation) - at_risk
recoveries = sum(
    1 - int(y)
    for bikes, y, _ in validation if bikes <= 2
)

print(
    f"New shortages: {new_shortages:,}/{at_risk:,} "
    f"({pct(new_shortages, at_risk):.1f}%)"
)
print(
    f"Recoveries: {recoveries:,}/{already_short:,} "
    f"({pct(recoveries, already_short):.1f}%)"
)


# ---------------------------------------------------------
# 2. Persistence performance by station-update activity
# ---------------------------------------------------------

print("\n=== PERSISTENCE ACCURACY ===")

groups = {
    "All validation hours": validation,
    "New station event": [
        row for row in validation if row[2]
    ],
    "No new station event": [
        row for row in validation if not row[2]
    ],
}

for name, rows in groups.items():
    correct = sum(
        int((bikes <= 2) == bool(y))
        for bikes, y, _ in rows
    )
    print(
        f"{name}: {pct(correct, len(rows)):.1f}% "
        f"({correct:,}/{len(rows):,})"
    )


# ---------------------------------------------------------
# 3. Empirical onset probabilities from training data
# ---------------------------------------------------------

train_groups = defaultdict(lambda: [0, 0])

for bikes, target in training:
    key = bike_group(bikes)
    train_groups[key][0] += 1
    train_groups[key][1] += int(target)

# Constant probability estimated from training.
# Laplace smoothing prevents exact zero/one probabilities.
prior = (
    sum(int(y) for _, y in training) + 1
) / (len(training) + 2)

# Probability for each bike-count category.
risks = {
    key: (positive + 1) / (count + 2)
    for key, (count, positive) in train_groups.items()
}

val_groups = defaultdict(lambda: [0, 0])
onset_val = []

for bikes, target, _ in validation:
    if bikes > 2:
        key = bike_group(bikes)
        val_groups[key][0] += 1
        val_groups[key][1] += int(target)
        onset_val.append((key, int(target)))


print("\n=== NEW SHORTAGE RISK BY CURRENT BIKES ===")
print("Bikes  Train N  Train risk  Val N  Actual val risk")

for key in ("3", "4", "5", "6-8", "9+"):
    n_train = train_groups[key][0]
    n_val, y_val = val_groups[key]

    print(
        f"{key:>5} {n_train:>8,} "
        f"{risks.get(key, prior):>10.1%} "
        f"{n_val:>7,} "
        f"{pct(y_val, n_val):>14.1f}%"
    )


# ---------------------------------------------------------
# 4. Probabilistic baseline comparison
# ---------------------------------------------------------

print("\n=== PROBABILISTIC ONSET BASELINES ===")

if onset_val:
    brier_constant = sum(
        (prior - y) ** 2
        for _, y in onset_val
    ) / len(onset_val)

    brier_buckets = sum(
        (risks.get(key, prior) - y) ** 2
        for key, y in onset_val
    ) / len(onset_val)

    print(f"Training onset rate: {prior:.2%}")
    print(f"Constant-risk Brier: {brier_constant:.5f}")
    print(f"Bike-count Brier:    {brier_buckets:.5f}")
    print("Lower Brier score is better.")

print("\nTest period remains untouched.")

con.close()