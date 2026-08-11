"""Check collected rows against invariants that must always hold.

    python -m bench.check_data            # all runs
    python -m bench.check_data --quiet    # violations only

Two bugs in this project produced confidently wrong numbers rather than errors:
a batch matched by proximity gave a t2 nine seconds before its own submission,
and a dispute game found by binary search over a non-monotonic sequence gave 196
transactions a trustless timestamp 209 days before they were sent. Neither
crashed. Both were caught by a human noticing an implausible median, which is
not a control - it is luck.

    WHAT MAKES A GOOD INVARIANT HERE

Something that is impossible rather than merely surprising. A batch cannot be
posted before the transaction it carries exists. A proof cannot precede the
batch it proves. A transaction cannot appear twice with different outcomes.
Those hold regardless of which rollup, which workload, or what the network was
doing, so a violation is always a bug in this code and never a fact about the
world.

Rules that would fire on unusual-but-real data are deliberately absent: a slow
batch, a long queue and a failed submission are all things the study exists to
measure.

Exit status is 0 when every row passes, 1 otherwise, so cron can gate on it.
"""

import argparse
from collections import Counter, defaultdict

from bench.core.records import Outcome, iter_runs, load

#: Finality is ordered by construction: a transaction is accepted, then its
#: batch is posted, then that batch is proven. Any inversion is a bug.
ORDER = ("t0", "t1", "t2", "t3")


def violations(rows: list[dict]) -> list[tuple[str, str]]:
    """(rule, detail) for everything wrong with these rows."""
    found: list[tuple[str, str]] = []
    by_hash: dict[str, list[dict]] = defaultdict(list)

    for row in rows:
        tag = f"{(row.get('hash') or 'no-hash')[:16]} in {row.get('run_id')}"

        # 1. Timestamps must not run backwards - but only where the ordering
        #    is genuinely guaranteed.
        #
        #    t0 anchors everything: nothing about a transaction can precede its
        #    own broadcast. t2 must precede t3, because a batch is proven after
        #    it is posted.
        #
        #    t1 deliberately anchors nothing. It is when we OBSERVED a receipt,
        #    not when the sequencer produced one, and it carries up to the poll
        #    interval of lag - measured at a median of 17.95s on batched
        #    submissions. A batch genuinely can be posted to the L1 after the L2
        #    block was produced but before our poll noticed it, which makes
        #    t2 < t1 an ordinary event rather than an impossible one. An earlier
        #    version of this rule flagged 73 such rows as corrupt; they were
        #    fine, and the rule was wrong.
        t0 = row.get("t0")
        if t0:
            for name in ("t1", "t2", "t3"):
                value = row.get(name)
                if value and value < t0:
                    found.append((
                        "timestamp precedes submission",
                        f"{tag}: {name} is {t0 - value:,.0f}s before t0",
                    ))
        if row.get("t2") and row.get("t3") and row["t3"] < row["t2"]:
            found.append((
                "proven before posted",
                f"{tag}: t3 is {row['t2'] - row['t3']:,.0f}s before t2",
            ))

        # The sound version of the t1 comparison: the batch cannot be posted
        # before the L2 block carrying the transaction was itself produced.
        if row.get("t2") and row.get("l2_block_ts") and row["t2"] < row["l2_block_ts"]:
            found.append((
                "batch posted before its L2 block",
                f"{tag}: t2 is {row['l2_block_ts'] - row['t2']:,.0f}s "
                "before the L2 block timestamp",
            ))

        # 2. A successful transaction has a hash; a rejected one must not.
        if row.get("outcome") == Outcome.SUCCESS and not row.get("hash"):
            found.append(("success without a hash", tag))
        if row.get("outcome") == Outcome.REJECTED and row.get("hash"):
            found.append((
                "rejected with a hash",
                f"{tag}: a refused transaction has no hash to record",
            ))

        # 3. A timestamp we cannot characterise is worse than none.
        if row.get("t3") and not row.get("t3_kind"):
            found.append(("t3 without a kind", tag))
        if row.get("t2") and not row.get("t2_kind"):
            found.append(("t2 without a kind", tag))

        # 4. Our transactions cannot outnumber the batch carrying them.
        if row.get("batch_tx_count") and row.get("batch") is None:
            found.append(("batch count without a batch", tag))

        # 5. A settled row needs the hash that proves it settled.
        if row.get("t2") and not row.get("l1_commit_tx"):
            found.append((
                "t2 without a commit hash",
                f"{tag}: a settlement time nobody else can check",
            ))

        if row.get("hash"):
            by_hash[row["hash"]].append(row)

    # 6. One transaction, one fate.
    for tx_hash, group in by_hash.items():
        outcomes = {r.get("outcome") for r in group}
        if len(group) > 1 and len(outcomes) > 1:
            found.append((
                "same hash, different outcomes",
                f"{tx_hash[:16]}: {sorted(o for o in outcomes if o)}",
            ))

    # 7. Our rows cannot exceed the batch's own transaction count.
    per_batch: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("batch") is not None:
            per_batch[(row.get("network"), row["batch"])].append(row)
    for (network, batch), group in per_batch.items():
        count = next((r["batch_tx_count"] for r in group
                      if r.get("batch_tx_count")), None)
        if count and len(group) > count:
            found.append((
                "more of our rows than the batch holds",
                f"{network} batch {batch}: {len(group)} ours of {count} total",
            ))

    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="print violations only")
    args = ap.parse_args()

    rows = [row for path in iter_runs() for row in load(path)]
    if not rows:
        print("no rows in bench/results/")
        return 0

    found = violations(rows)
    settled = sum(1 for r in rows if r.get("t3"))

    if not args.quiet:
        print(f"\n{len(rows):,} rows, {settled:,} settled through t3")

    if not found:
        if not args.quiet:
            print("every invariant holds\n")
        return 0

    grouped = Counter(rule for rule, _detail in found)
    print(f"\n{len(found)} violation(s):\n")
    for rule, count in grouped.most_common():
        print(f"  {count:>5}x  {rule}")
        for seen, (name, detail) in enumerate(
                [f for f in found if f[0] == rule]):
            if seen >= 3:
                print(f"          ... and {count - 3} more")
                break
            print(f"          {detail}")
    print("\nThese are impossible states, not unusual measurements. Each one is "
          "a bug in the collection code.\n")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
