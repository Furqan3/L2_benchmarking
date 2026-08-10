"""Export raw rows and a derived summary (tasks D1, D2, D3, D4).

    python -m bench.export                    # raw CSV + summary, to bench/export/
    python -m bench.export --eth-usd 4200     # override the configured rate

Two files, and the distinction between them is the point:

    raw_transactions.csv   one row per transaction, every timestamp absolute,
                           every hash present. This is the primary artefact.
    summary.csv            statistics derived from the raw rows. Regenerable,
                           and never a replacement for them.

    LATENCIES ARE COMPUTED HERE, NOT DURING COLLECTION

The run files hold four absolute timestamps and no durations (C5). Every
latency in this export is t_n minus t0, computed at this moment from those
absolutes, so an arithmetic error is a re-run of this script rather than a
re-run of the experiment.

    EVERY DOLLAR CARRIES ITS ASSUMPTIONS

The ETH rate and the L1 gas price appear beside every cost, in the summary and
in a stated-assumptions block. A figure without them cannot be checked by
anyone, including us in six weeks.
"""

import argparse
import csv
import datetime as dt
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

from bench.core.costs import BLOB_BYTES, L1Cost, l1_cost, pricing, to_usd
from bench.core.networks import connect_verified, load_networks
from bench.core.records import Outcome, iter_runs, load

REPO_ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = REPO_ROOT / "bench" / "export"

# Column order for the raw export. Explicit rather than derived from the record
# so that adding an internal field does not silently reorder a published file.
RAW_COLUMNS = [
    "run_id", "network", "chain_id", "workload", "outcome", "detail",
    "hash", "l1_commit_tx", "l1_prove_tx", "l1_execute_tx",
    "t0", "t1", "t2", "t3", "l2_block_ts",
    "t1_minus_t0_s", "t2_minus_t0_s", "t3_minus_t0_s",
    "t3_kind", "t3_source",
    "batch", "batch_tx_count", "block", "l1_commit_block", "l1_prove_block",
    "nonce", "gas_limit", "gas_used", "gas_price_wei",
]


def latencies(row: dict) -> dict:
    """The three latencies, each from t0. None where a stage has not settled."""
    t0 = row.get("t0")
    out = {}
    for key in ("t1", "t2", "t3"):
        value = row.get(key)
        out[f"{key}_minus_t0_s"] = (value - t0) if (t0 and value) else None
    return out


def write_raw(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RAW_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, **latencies(row)})


def batch_costs(rows: list[dict], networks) -> dict[tuple, dict]:
    """L1 cost and DA size per distinct settlement batch (D2, D3).

    Keyed by (network, batch). Fetched once per batch rather than once per
    transaction - a hundred rows in one batch describe one pair of L1
    transactions, and querying them a hundred times would only invite a rate
    limit.
    """
    out: dict[tuple, dict] = {}
    by_batch: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        if row.get("batch") is not None and row.get("l1_commit_tx"):
            by_batch[(row["network"], row["batch"])].append(row)

    connections: dict[str, object] = {}
    for (network_key, batch), group in by_batch.items():
        net = networks.get(network_key)
        if net is None or not net.settles_on:
            continue
        if net.settles_on not in connections:
            connections[net.settles_on] = connect_verified(
                networks[net.settles_on]
            )
        w3_l1 = connections[net.settles_on]

        sample = group[0]
        entry: dict = {
            "network": network_key,
            "batch": batch,
            "batch_tx_count": sample.get("batch_tx_count"),
            "our_tx_count": len(group),
            "commit": None,
            "prove": None,
        }
        for stage, key in (("commit", "l1_commit_tx"), ("prove", "l1_prove_tx")):
            tx_hash = sample.get(key)
            if not tx_hash:
                continue
            try:
                entry[stage] = l1_cost(w3_l1, tx_hash)
            except Exception as exc:  # noqa: BLE001
                entry[f"{stage}_error"] = f"{type(exc).__name__}: {exc}"[:80]
        out[(network_key, batch)] = entry
    return out


def summarise_group(rows: list[dict]) -> dict:
    """Latency statistics for one network/workload cell (D4 step 1)."""
    ok = [r for r in rows if r["outcome"] in Outcome.MEASURABLE]
    stats: dict = {
        "transactions": len(rows),
        "successes": len(ok),
        "success_rate": (len(ok) / len(rows)) if rows else 0.0,
    }
    for key, label in (("t1", "full_trust"), ("t2", "partial_trust"),
                       ("t3", "trustless")):
        values = sorted((r[key] - r["t0"]) for r in ok if r.get(key) and r.get("t0"))
        if not values:
            stats[f"{label}_median_s"] = None
            stats[f"{label}_n"] = 0
            continue
        stats[f"{label}_n"] = len(values)
        stats[f"{label}_median_s"] = st.median(values)
        stats[f"{label}_p95_s"] = values[max(0, int(len(values) * 0.95) - 1)]
        stats[f"{label}_min_s"] = values[0]
        stats[f"{label}_max_s"] = values[-1]

    # Achieved throughput, never peak. We did not saturate the sequencer and
    # must not try: finding its breaking point would degrade a service other
    # people depend on, and would measure our own rate limits anyway (D4).
    if ok:
        span = max(r["t1"] for r in ok if r.get("t1")) - min(r["t0"] for r in ok)
        stats["achieved_tps_full_trust"] = (len(ok) / span) if span > 0 else None
        stats["wall_clock_s"] = span
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eth-usd", type=float, default=None,
                    help="override the rate in pricing.yaml")
    ap.add_argument("--out", default=str(EXPORT_DIR),
                    help="output directory (default bench/export/)")
    args = ap.parse_args()

    rows = [r for path in iter_runs() for r in load(path)]
    if not rows:
        print("no rows in bench/results/")
        return 1

    price = pricing()
    eth_usd = args.eth_usd if args.eth_usd is not None else float(price.get("rate", 0))
    rate_source = "--eth-usd" if args.eth_usd is not None else "pricing.yaml"

    out_dir = Path(args.out)
    write_raw(rows, out_dir / "raw_transactions.csv")

    networks = load_networks()
    costs = batch_costs(rows, networks)

    # --- summary -----------------------------------------------------------
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["network"], row["workload"])].append(row)

    summary_rows = []
    for (network_key, workload), group in sorted(groups.items()):
        entry = {"network": network_key, "workload": workload}
        entry.update(summarise_group(group))

        # Per-transaction cost share, from the batches these rows settled in.
        share_wei = 0
        da_bytes = 0
        counted = 0
        for row in group:
            key = (row["network"], row.get("batch"))
            batch = costs.get(key)
            if not batch or not batch.get("batch_tx_count"):
                continue
            total = sum(c.total_wei for c in
                        (batch.get("commit"), batch.get("prove")) if c)
            share_wei += total / batch["batch_tx_count"]
            commit: L1Cost | None = batch.get("commit")
            if commit:
                da_bytes += commit.da_bytes / batch["batch_tx_count"]
            counted += 1
        if counted:
            entry["l1_cost_per_tx_wei"] = share_wei / counted
            entry["l1_cost_per_tx_usd"] = to_usd(share_wei / counted, eth_usd)

            # Named for what it is: the batch's DA divided by the batch's
            # transaction count. It is NOT a per-workload measurement.
            #
            # A blob is a fixed 131,072 bytes whether the rollup fills it or
            # not, and this batch carried 875 transactions of which 109 were
            # ours. So this average is identical for every workload in the
            # batch and is dominated by traffic we did not generate. Reporting
            # it as "DA bytes for an ERC-20 transfer" would be exactly the
            # fixed-byte-count-regardless-of-workload that D3 warns against.
            entry["da_bytes_per_tx_batch_avg"] = da_bytes / counted
            entry["da_workload_sensitive"] = False
            entry["da_note"] = (
                "batch average over all users; blob DA is quantised to whole "
                "blobs so it does not vary with workload at this volume"
            )

        # The workload-sensitive figure that IS measurable. zkSync prices
        # pubdata inside L2 gas at gasPerPubdata, so gas_used separates the
        # workloads cleanly where blob bytes cannot. Reported as gas, not as
        # bytes, because that is what was actually observed.
        used = [r["gas_used"] for r in group if r.get("gas_used")]
        if used:
            entry["l2_gas_used_median"] = st.median(used)
        entry["eth_usd_rate"] = eth_usd
        entry["eth_usd_source"] = rate_source
        summary_rows.append(entry)

    fields = sorted({k for r in summary_rows for k in r})
    fields = (["network", "workload"]
              + [f for f in fields if f not in ("network", "workload")])
    with (out_dir / "summary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary_rows)

    # --- stated assumptions ------------------------------------------------
    lines = [
        "Stated assumptions for every cost figure in this export",
        "=" * 56,
        f"generated            {dt.datetime.now().isoformat(timespec='seconds')}",
        f"ETH/USD rate         {eth_usd}  (from {rate_source})",
        f"rate recorded at     {price.get('recorded_at', 'UNRECORDED')}",
        f"rate source          {str(price.get('source', 'UNRECORDED')).strip()}",
        "",
        "Costs are taken from the gasUsed and effectiveGasPrice on the L1",
        "receipts of the batch commit and proof transactions. Never from gas",
        "estimates, which overshoot actual usage by up to 9x on zkSync.",
        "",
        "Per-transaction cost is the batch's total L1 cost divided by the",
        "number of transactions in that batch - everybody's, not only ours.",
        "",
        "Throughput is ACHIEVED throughput at our chosen submission rate.",
        "The networks were not saturated and no peak figure is reported.",
        "",
        "DATA AVAILABILITY - A DECLARED LIMITATION",
        "Blob DA is quantised: a blob costs 131,072 bytes whether the rollup",
        "fills it or not, and a batch carries every user's transactions, not",
        "only ours. So per-transaction DA bytes is a batch average that does",
        "not vary with workload at the volumes measured here, and D3's test -",
        "DA bytes differing between a native and an ERC-20 batch - is not",
        "reachable on this rollup at this scale. The column is named",
        "da_bytes_per_tx_batch_avg and flagged da_workload_sensitive=False",
        "rather than presented as a per-workload measurement.",
        "The workload difference is real but shows up in L2 gas, where zkSync",
        "prices pubdata at gasPerPubdata: see l2_gas_used_median.",
        "",
        "Batches observed",
        "-" * 56,
    ]
    for (network_key, batch), entry in sorted(costs.items()):
        commit: L1Cost | None = entry.get("commit")
        prove: L1Cost | None = entry.get("prove")
        lines.append(f"{network_key} batch {batch}: "
                     f"{entry['our_tx_count']} of {entry['batch_tx_count']} "
                     f"transactions ours")
        if commit:
            lines.append(
                f"  commit  type {commit.tx_type}  {commit.da_kind}  "
                f"{commit.da_bytes:,} bytes  "
                f"gas {commit.gas_used:,} @ "
                f"{commit.effective_gas_price / 1e9:.3f} gwei  "
                f"= {commit.total_wei / 1e18:.8f} ETH"
            )
            if commit.blob_count:
                lines.append(
                    f"          {commit.blob_count} blob(s) x {BLOB_BYTES:,} B, "
                    f"blob gas {commit.blob_gas_used:,} @ "
                    f"{commit.blob_gas_price / 1e9:.6f} gwei"
                )
            if commit.unavailable:
                lines.append(f"          unavailable: "
                             f"{', '.join(commit.unavailable)}")
        if prove:
            lines.append(
                f"  prove   type {prove.tx_type}  gas {prove.gas_used:,} @ "
                f"{prove.effective_gas_price / 1e9:.3f} gwei  "
                f"= {prove.total_wei / 1e18:.8f} ETH"
            )

    (out_dir / "assumptions.txt").write_text("\n".join(lines) + "\n")

    # --- E3: one table, both architectures, all three levels ---------------
    comparison = [
        "Cross-architecture comparison (task E3)",
        "=" * 78,
        f"ETH/USD {eth_usd} from {rate_source}. Latency medians over successes.",
        "",
        f"{'rollup':<22}{'family':<12}{'level':<16}{'median':>12}{'kind':>12}",
        "-" * 78,
    ]
    for (network_key, workload), group in sorted(groups.items()):
        net = networks.get(network_key)
        ok = [r for r in group if r["outcome"] in Outcome.MEASURABLE]
        if not ok:
            continue
        comparison.append(f"{network_key} / {workload}")
        for key, label in (("t1", "full trust"), ("t2", "partial trust"),
                           ("t3", "trustless")):
            values = sorted(r[key] - r["t0"] for r in ok
                            if r.get(key) and r.get("t0"))
            if not values:
                comparison.append(f"{'':<22}{'':<12}{label:<16}{'unavailable':>12}")
                continue
            median = st.median(values)
            # Units chosen per magnitude: the three levels span seconds to days
            # and one shared unit makes the interesting one unreadable.
            if median < 120:
                shown = f"{median:.2f} s"
            elif median < 7200:
                shown = f"{median / 60:.2f} m"
            else:
                shown = f"{median / 86400:.2f} d"
            kinds = {r.get(f"{key}_kind") for r in ok if r.get(key)}
            kind = "/".join(sorted(k for k in kinds if k)) or "observed"
            comparison.append(
                f"{'':<22}{(net.family or '-'):<12}{label:<16}{shown:>12}{kind:>12}"
            )
        # Cost, where the rollup exposes enough to attribute it.
        entry = next((e for e in summary_rows
                      if e["network"] == network_key
                      and e["workload"] == workload), None)
        if entry and entry.get("l1_cost_per_tx_usd") is not None:
            comparison.append(f"{'':<22}{'':<12}{'L1 cost / tx':<16}"
                              f"{'$' + format(entry['l1_cost_per_tx_usd'], '.8f'):>12}")
        else:
            comparison.append(f"{'':<22}{'':<12}{'L1 cost / tx':<16}"
                              f"{'unavailable':>12}")
        comparison.append("")

    comparison += [
        "READING THIS TABLE",
        "-" * 78,
        "The 'kind' column is not decoration. A ZK rollup's trustless finality is",
        "OBSERVED: a proof was verified in an Ethereum block whose timestamp we",
        "read. An optimistic rollup's is DERIVED: it is the moment a challenge",
        "window closes, and nothing happens then - no transaction, no event. It is",
        "a deadline we computed from the output proposal plus the challenge",
        "period read from the OptimismPortal.",
        "",
        "Those two numbers must not be compared as though they were the same kind",
        "of measurement, and the difference is the point rather than a caveat.",
        "",
        "'estimated' on partial trust means the batch transaction was matched by",
        "block range, not named by the rollup. OP Stack chains expose no mapping",
        "from an L2 transaction to the batch carrying it without decoding the blob.",
        "",
        "L1 cost is unavailable for OP Stack for the same reason: the number of",
        "transactions sharing a batch is not exposed, so there is no denominator.",
        "Reported unavailable rather than divided by a guess.",
    ]
    (out_dir / "comparison.txt").write_text("\n".join(comparison) + "\n")

    print(f"\nwrote {out_dir}/")
    print(f"  raw_transactions.csv   {len(rows)} rows")
    print(f"  summary.csv            {len(summary_rows)} cells")
    print(f"  assumptions.txt        ETH/USD {eth_usd} from {rate_source}")
    print(f"\noutcomes: {dict(Counter(r['outcome'] for r in rows))}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
