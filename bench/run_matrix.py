"""Drive the experiment matrix (tasks F1, F2, F3, and E3's pairing).

    python -m bench.run_matrix --plan             # the checklist, and what is done
    python -m bench.run_matrix --next             # run the next due cell, then stop
    python -m bench.run_matrix --cell zk_native_10 --rep 2
    python -m bench.run_matrix --paired -w native_transfer -c 50

Every cell in the matrix has an identifier of the form

    <rollup>_<workload>_n<batch size>

and each of its repetitions is a run whose run_id is that cell plus a repetition
number and a timestamp. The identifier lands on every row, so any figure can be
traced back to the cell and repetition that produced it.

    WHY THIS EXISTS RATHER THAN A SHELL LOOP

Two reasons a loop cannot cover. First, repetitions have to be separated in
time - five runs back to back measure one moment five times - so the runner has
to know when each cell last ran and refuse to run it again too soon. Second,
E3's comparison requires the two architectures be submitted as close together
as possible, which is a pairing constraint rather than an iteration order.

Exit status is 0 when the requested work completed.
"""

import argparse
import datetime as dt
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

import yaml

from bench.core.records import RESULTS_DIR, iter_runs, load

REPO_ROOT = Path(__file__).resolve().parents[1]
MATRIX_CONFIG = REPO_ROOT / "bench" / "configs" / "matrix.yaml"

SHORT = {"zksync_sepolia": "zk", "op_sepolia": "op",
         "polygon_zkevm_cardona": "pzk", "arbitrum_sepolia": "arb"}


def config() -> dict:
    return yaml.safe_load(MATRIX_CONFIG.read_text()) or {}


def cell_id(rollup: str, workload: str, size: int) -> str:
    return f"{SHORT.get(rollup, rollup)}_{workload.replace('_transfer', '')}_n{size}"


def cells(cfg: dict) -> list[tuple[str, str, str, int]]:
    """(cell id, rollup, workload, batch size) for the whole matrix."""
    out = []
    for rollup in cfg.get("rollups", []):
        for workload in cfg.get("workloads", []):
            for size in cfg.get("batch_sizes", []):
                out.append((cell_id(rollup, workload, size), rollup, workload, size))
    return out


def completed() -> dict[str, list[float]]:
    """Submission times of the runs already on disk, keyed by cell id.

    Read from the rows rather than from a ledger, so the record of what has run
    cannot drift from the data. Deleting a run file genuinely un-runs it.
    """
    seen: dict[str, list[float]] = defaultdict(list)
    for path in iter_runs():
        rows = load(path)
        if not rows:
            continue
        run_id = rows[0].get("run_id") or path.stem
        # run_id is "<cell>__rep<N>__<stamp>" for matrix runs; anything else is
        # an ad-hoc run and is not counted towards a cell.
        if "__rep" not in run_id:
            continue
        cell = run_id.split("__rep")[0]
        starts = [r["t0"] for r in rows if r.get("t0")]
        if starts:
            seen[cell].append(min(starts))
    return seen


def submit(rollup: str, workload: str, size: int, run_id: str) -> int:
    """One run, as a subprocess, so a crash cannot take the matrix with it."""
    command = [sys.executable, "-m", "bench.submit_run",
               "-n", rollup, "-w", workload, "-c", str(size),
               "--run-id", run_id]
    print(f"\n$ {' '.join(command[2:])}")
    return subprocess.call(command)


def plan(cfg: dict) -> int:
    done = completed()
    target = int(cfg.get("repetitions", 5))
    gap = float(cfg.get("min_gap_seconds", 3600))
    now = time.time()

    print(f"\n{'cell':<26}{'reps':>6}{'target':>8}   next due")
    print("-" * 68)
    outstanding = 0
    for cell, _rollup, _workload, _size in cells(cfg):
        runs = sorted(done.get(cell, []))
        count = len(runs)
        if count >= target:
            due = "complete"
        elif not runs:
            due = "now"
        else:
            wait = runs[-1] + gap - now
            due = "now" if wait <= 0 else f"in {wait / 60:.0f} min"
        if count < target:
            outstanding += target - count
        print(f"{cell:<26}{count:>6}{target:>8}   {due}")
    print(f"\n{outstanding} repetition(s) outstanding across "
          f"{len(cells(cfg))} cells")
    print(f"minimum gap between repetitions of one cell: {gap / 60:.0f} min\n")
    return 0


def run_next(cfg: dict, force: bool = False) -> int:
    """Run the single most-overdue cell, then stop.

    One cell per invocation on purpose: this is meant to be called repeatedly
    from a scheduler over hours, which is what actually separates repetitions
    in time. A loop that ran the whole matrix in one go would defeat F2.
    """
    done = completed()
    target = int(cfg.get("repetitions", 5))
    gap = float(cfg.get("min_gap_seconds", 3600))
    now = time.time()

    candidates = []
    for cell, rollup, workload, size in cells(cfg):
        runs = sorted(done.get(cell, []))
        if len(runs) >= target:
            continue
        last = runs[-1] if runs else 0.0
        if not force and runs and now - last < gap:
            continue
        candidates.append((len(runs), last, cell, rollup, workload, size))

    if not candidates:
        print("nothing due. --plan shows when the next repetition unlocks.")
        return 0

    # Fewest repetitions first, then least recently run: breadth before depth,
    # so an interrupted matrix still has every cell represented.
    candidates.sort(key=lambda c: (c[0], c[1]))
    reps, _last, cell, rollup, workload, size = candidates[0]
    run_id = (f"{cell}__rep{reps + 1}__"
              f"{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}")
    print(f"cell {cell}, repetition {reps + 1} of {target}")
    return submit(rollup, workload, size, run_id)


def run_paired(cfg: dict, workload: str, size: int) -> int:
    """Submit the same workload to both rollups back to back (E3 step 1).

    Sequential rather than concurrent: two batches from one account on two
    chains are independent in nonce terms, but submitting them concurrently
    would interleave their RPC traffic and blur the very thing being measured.
    The gap between them is reported so a reader can judge it.
    """
    pairing = cfg.get("pairing", {})
    if not pairing.get("enabled", False):
        print("pairing.enabled is false in matrix.yaml - nothing submitted.")
        return 2

    rollups = cfg.get("rollups", [])
    skew_limit = float(pairing.get("max_skew_seconds", 300))
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")

    started = time.time()
    status = 0
    for rollup in rollups:
        run_id = f"{cell_id(rollup, workload, size)}__rep1__paired-{stamp}"
        status |= submit(rollup, workload, size, run_id)
    skew = time.time() - started

    print(f"\npaired run complete: both rollups within {skew:.0f}s")
    if skew > skew_limit:
        print(f"WARNING: skew exceeds max_skew_seconds ({skew_limit:.0f}s). "
              "L1 gas may have moved between the two; both runs recorded it, "
              "so check before treating these as one comparison.")
    return status


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--plan", action="store_true", help="show the checklist")
    ap.add_argument("--next", action="store_true", help="run the next due cell")
    ap.add_argument("--paired", action="store_true",
                    help="submit one workload to both rollups back to back")
    ap.add_argument("-w", "--workload", default="native_transfer")
    ap.add_argument("-c", "--count", type=int, default=50)
    ap.add_argument("--force", action="store_true",
                    help="ignore the minimum gap between repetitions")
    args = ap.parse_args()

    cfg = config()
    if args.paired:
        return run_paired(cfg, args.workload, args.count)
    if args.next:
        return run_next(cfg, force=args.force)
    return plan(cfg)


if __name__ == "__main__":
    raise SystemExit(main())
