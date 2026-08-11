"""Quantify how much results vary between days (task G4).

    python -m bench.analysis.reproducibility

Takes every matrix cell that has been run on more than one calendar day and
compares the days against each other, from the committed configuration rather
than from a remembered command line.

    WHAT A DIFFERENCE HERE MEANS

Not that something is broken. These are public networks under other people's
load, and day-to-day variation is a property of the system being measured, not
noise to be averaged away. The task asks for the divergence to be quantified and
reported, because a reader deciding whether to trust a median needs to know
whether it would have been the same median yesterday.

A cell that diverges wildly is a finding about network variability. A cell that
does not is evidence the measurement is stable. Both are worth stating; only
silence is not.

Exit status is 0 when at least one cell could be compared.
"""

import datetime as dt
import statistics as st
from collections import defaultdict
from pathlib import Path

from bench.core.records import Outcome, iter_runs, load

REPO_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = REPO_ROOT / "bench" / "analysis" / "figures" / "g4_reproducibility.csv"

LEVELS = (("t1", "full trust"), ("t2", "partial trust"), ("t3", "trustless"))


def by_cell_and_day() -> dict[str, dict[str, list[dict]]]:
    """Rows grouped by matrix cell, then by the UTC date they were submitted."""
    out: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for path in iter_runs():
        for row in load(path):
            run_id = row.get("run_id") or ""
            if "__rep" not in run_id or row["outcome"] not in Outcome.MEASURABLE:
                continue
            if not row.get("t0"):
                continue
            cell = run_id.split("__rep")[0]
            day = dt.datetime.utcfromtimestamp(row["t0"]).strftime("%Y-%m-%d")
            out[cell][day].append(row)
    return out


def main() -> int:
    grouped = by_cell_and_day()
    multi = {cell: days for cell, days in grouped.items() if len(days) > 1}

    if not multi:
        print("\nNo cell has been run on more than one day yet.")
        print("G4 needs repetitions spanning at least two dates; the hourly "
              "collector produces them on its own.\n")
        return 1

    print(f"\n{len(multi)} cell(s) run on more than one day\n")
    header = (f"{'cell':<20}{'level':<15}{'day':<12}{'n':>5}"
              f"{'median':>12}{'vs other day':>15}")
    print(header)
    print("-" * len(header))

    table: list[list] = []
    divergences: list[float] = []

    for cell in sorted(multi):
        days = multi[cell]
        for key, label in LEVELS:
            medians: dict[str, float] = {}
            counts: dict[str, int] = {}
            for day, rows in sorted(days.items()):
                values = [r[key] - r["t0"] for r in rows if r.get(key)]
                if values:
                    medians[day] = st.median(values)
                    counts[day] = len(values)
            if len(medians) < 2:
                continue

            baseline = medians[sorted(medians)[0]]
            for day in sorted(medians):
                delta = ((medians[day] - baseline) / baseline * 100
                         if baseline else 0.0)
                shown = "baseline" if day == sorted(medians)[0] else f"{delta:+.1f}%"
                print(f"{cell:<20}{label:<15}{day:<12}{counts[day]:>5}"
                      f"{medians[day]:>11.2f}s{shown:>15}")
                table.append([cell, label, day, counts[day],
                              f"{medians[day]:.2f}", shown])
                if day != sorted(medians)[0]:
                    divergences.append(abs(delta))
        print()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    import csv
    with OUTPUT.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cell", "level", "day", "n", "median_s", "vs_baseline"])
        writer.writerows(table)

    if divergences:
        print(f"Day-to-day divergence across {len(divergences)} comparisons: "
              f"median {st.median(divergences):.1f}%, "
              f"worst {max(divergences):.1f}%")
        print("\nThis is the number G4 asks for. It is a property of public "
              "networks under other people's load, not an error term - and a "
              "reader deciding whether to trust a median needs it.")
    print(f"\nwrote {OUTPUT}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
