"""Fill in t2 and t3 for rows already collected (tasks C2-C6).

    python -m bench.resolve_run                  # every run file
    python -m bench.resolve_run --run 20260810-131324_...
    python -m bench.resolve_run --watch 600      # re-run every 10 minutes

The resolve half of the two-pass design. It reads run files, finds rows still
missing t2 or t3, asks the rollup where they settled, and writes the files back.
Safe to run repeatedly and safe to interrupt: it recomputes what is outstanding
from the file each time and never depends on anything held in memory from an
earlier pass.

    WHY THIS IS SEPARATE FROM SUBMISSION

Trustless finality can take an hour or more. A program that waits synchronously
ties up the machine, dies when the laptop sleeps, and loses everything it had
collected. Splitting the two is what lets data collection start in week three
and keep running while the rest of the framework is still being built - and
without it every experiment costs its full wall-clock duration in blocked time.

Exit status is 0 when nothing is outstanding, 1 when rows remain unresolved.
"""

import argparse
import time

from bench.adapters import for_network
from bench.adapters.base import SettlementUnavailable
from bench.core.networks import Network, connect_verified, load_networks
from bench.core.records import iter_runs, load, rewrite, run_path
from bench.core.settle import apply_settlement, outstanding


def resolve_file(path, networks: dict[str, Network], verbose: bool = True) -> dict:
    """One pass over one run file. Returns counts."""
    rows = load(path)
    if not rows:
        return {"rows": 0, "pending": 0, "resolved_t2": 0, "resolved_t3": 0}

    pending = [r for r in rows if outstanding(r)]
    stats = {
        "rows": len(rows),
        "pending": len(pending),
        "resolved_t2": 0,
        "resolved_t3": 0,
        "errors": 0,
    }
    if not pending:
        return stats

    # Rows in one file share a network, but grouping rather than assuming keeps
    # this correct if a run file is ever merged from several.
    by_network: dict[str, list[dict]] = {}
    for row in pending:
        by_network.setdefault(row["network"], []).append(row)

    for network_key, group in by_network.items():
        net = networks.get(network_key)
        if net is None:
            print(f"  unknown network '{network_key}' - skipped")
            continue
        l1_key = net.settles_on
        if not l1_key or l1_key not in networks:
            print(f"  {network_key}: no settles_on network configured")
            continue

        try:
            adapter = for_network(net)
            w3_l2 = connect_verified(net)
            w3_l1 = connect_verified(networks[l1_key])
        except SettlementUnavailable as exc:
            print(f"  {network_key}: {exc}")
            continue
        except Exception as exc:  # noqa: BLE001
            print(f"  {network_key}: cannot connect - {type(exc).__name__}: {exc}")
            continue

        for row in group:
            had_t2, had_t3 = row.get("t2"), row.get("t3")
            try:
                settlement = adapter.settlement(w3_l2, row["hash"])
            except SettlementUnavailable as exc:
                stats["errors"] += 1
                if verbose and stats["errors"] == 1:
                    print(f"  {network_key}: {exc}")
                continue
            except Exception as exc:  # noqa: BLE001
                stats["errors"] += 1
                if verbose and stats["errors"] == 1:
                    print(f"  {network_key}: {type(exc).__name__}: {exc}")
                continue

            apply_settlement(row, settlement, w3_l1)
            if had_t2 is None and row.get("t2") is not None:
                stats["resolved_t2"] += 1
            if had_t3 is None and row.get("t3") is not None:
                stats["resolved_t3"] += 1

    # Written back whatever happened, so partial progress survives. An
    # interrupted pass loses at most the rows it had not reached.
    rewrite(path, rows)
    stats["pending"] = sum(1 for r in rows if outstanding(r))
    return stats


def one_pass(run: str | None, verbose: bool = True) -> int:
    networks = load_networks()
    paths = [run_path(run)] if run else list(iter_runs())
    if not paths:
        print("no run files in bench/results/")
        return 0

    total_pending = 0
    for path in paths:
        stats = resolve_file(path, networks, verbose)
        if stats["rows"] == 0:
            continue
        total_pending += stats["pending"]
        flags = []
        if stats.get("resolved_t2"):
            flags.append(f"+{stats['resolved_t2']} t2")
        if stats.get("resolved_t3"):
            flags.append(f"+{stats['resolved_t3']} t3")
        if stats.get("errors"):
            flags.append(f"{stats['errors']} error(s)")
        note = ("  " + ", ".join(flags)) if flags else ""
        print(f"  {stats['rows']:>4} rows  {stats['pending']:>4} pending"
              f"{note}   {path.name}")

    print(f"\noutstanding: {total_pending} row(s)")
    return 0 if total_pending == 0 else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run", default=None, help="one run id instead of all")
    ap.add_argument("--watch", type=float, default=None, metavar="SECONDS",
                    help="repeat every N seconds until nothing is outstanding")
    args = ap.parse_args()

    if args.watch is None:
        return one_pass(args.run)

    while True:
        print(f"\n--- {time.strftime('%H:%M:%S')} ---")
        if one_pass(args.run) == 0:
            print("nothing outstanding - stopping")
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
