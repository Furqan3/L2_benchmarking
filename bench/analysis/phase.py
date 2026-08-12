"""Correct settlement latency for uneven sampling of the batch cycle.

    python -m bench.analysis.phase

A rollup posts batches on its own schedule. How long a transaction waits for its
batch therefore depends entirely on where in that cycle it was submitted:
just after a batch seals and it waits a full interval, just before and it waits
almost none. Averaged over uniformly distributed submissions, the expected wait
is half the interval.

    WHY THIS MODULE EXISTS

Collection ran hourly on a fixed minute while zkSync Sepolia commits every 120
minutes, so submissions only ever landed at two points in that cycle - and the
round-robin tied which workload a cell held to which of the two it got.
Measured over 1,269 settled rows, even-hour submissions waited a median of 25.5
minutes and odd-hour submissions 87.2. Pooling those without weighting gave 27.8
minutes, which is not an estimate of anything: it is the even-hour figure
lightly contaminated, because two thirds of the rows happened to be even-hour.

The estimator here bins rows by where in the batch cycle they were submitted,
takes a median within each bin, and averages the bins. Every part of the cycle
then counts once regardless of how often it was sampled.

The scheduler now jitters its submission time, so future collection needs no
correction. This exists because the data already gathered does.
"""

import argparse
import statistics as st
from collections import defaultdict

from bench.core.records import Outcome, iter_runs, load

#: Bins across one batch cycle. Twelve gives ten-minute resolution on a
#: two-hour interval - fine enough to see the ramp, coarse enough that each bin
#: holds rows.
BINS = 12


def batch_interval(rows: list[dict]) -> float | None:
    """Median seconds between consecutive batch commits, from the data itself.

    Taken from distinct settlement timestamps rather than assumed, so it stays
    correct if the rollup changes its cadence.
    """
    seen: dict[int, float] = {}
    for row in rows:
        if row.get("batch") is not None and row.get("t2"):
            seen.setdefault(row["batch"], row["t2"])
    stamps = sorted(seen.values())
    if len(stamps) < 3:
        return None
    gaps = [b - a for a, b in zip(stamps, stamps[1:])]
    return st.median(gaps)


def balanced(rows: list[dict], key: str, interval: float) -> dict | None:
    """Median of `key` minus t0, with every phase of the cycle weighted equally."""
    bins: dict[int, list[float]] = defaultdict(list)
    for row in rows:
        if not row.get(key) or not row.get("t0"):
            continue
        phase = (row["t0"] % interval) / interval
        bins[min(BINS - 1, int(phase * BINS))].append(row[key] - row["t0"])

    if len(bins) < 2:
        return None
    medians = [st.median(v) for v in bins.values()]
    raw = [v for values in bins.values() for v in values]
    return {
        "raw_median": st.median(raw),
        "balanced_median": st.mean(medians),
        "bins_covered": len(bins),
        "n": len(raw),
        "min": min(raw),
        "max": max(raw),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default="zksync_sepolia")
    args = ap.parse_args()

    rows = [r for p in iter_runs() for r in load(p)
            if r["network"] == args.network and r["outcome"] in Outcome.MEASURABLE]
    if not rows:
        print(f"no successful rows for {args.network}")
        return 1

    interval = batch_interval(rows)
    if interval is None:
        print("too few settled batches to estimate the interval")
        return 1

    print(f"\n{args.network}")
    print(f"  batch interval, measured from the data   {interval / 60:.1f} min")
    print(f"  expected wait for uniform submission     {interval / 120:.1f} min")
    print(f"\n  {'level':<8}{'n':>6}{'as collected':>15}{'phase-balanced':>17}"
          f"{'bins':>7}")
    print("  " + "-" * 55)

    for key, label in (("t2", "t2"), ("t3", "t3")):
        result = balanced(rows, key, interval)
        if not result:
            continue
        print(f"  {label:<8}{result['n']:>6}{result['raw_median'] / 60:>14.1f}m"
              f"{result['balanced_median'] / 60:>16.1f}m"
              f"{result['bins_covered']:>5}/{BINS}")

    result = balanced(rows, "t2", interval)
    if result:
        print(f"\n  t2 ranged {result['min'] / 60:.1f} to {result['max'] / 60:.1f} "
              f"minutes across the cycle.")
        print("  Report the balanced figure, or report the range and say why it "
              "is a range —")
        print("  a single median implies a typical wait that does not exist.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
