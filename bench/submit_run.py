"""Submit a batch and record it (tasks B2, B3, B4, B5).

    python -m bench.submit_run -n zksync_sepolia -w native_transfer -c 1
    python -m bench.submit_run -n zksync_sepolia -w erc20_transfer -c 50
    python -m bench.submit_run -n zksync_sepolia -w native_transfer -c 1 --dry-run

Builds a batch of one workload, numbers it consecutively from a single fetched
nonce, broadcasts in index order, waits for each receipt, and appends one JSON
object per transaction to bench/results/<run_id>.jsonl.

This is the submit half of what C6 will split in two. It already exits as soon
as t1 is known rather than waiting on settlement, so the rows it writes are the
rows the resolve pass will later fill in t2 and t3 on.

Exit status is 0 when every transaction succeeded, 1 otherwise.
"""

import argparse
import datetime as dt

from bench.core.networks import (
    ChainIdMismatch,
    Network,
    connect_verified,
    load_networks,
)
from bench.core.records import append, rewrite, run_path
from bench.core.submit import (
    SubmissionError,
    assign_nonces,
    estimate_gas,
    group_failures,
    rejected_record,
    resolve_t1_batch,
    submit,
    summarise,
    verify_nonce_advance,
)
from bench.core.wallet import load_account
from bench.core.workloads import TokenNotDeployed, build, load_workloads


def default_run_id(network_key: str, workload: str, count: int) -> str:
    """A readable, sortable identifier. F1 replaces this with a matrix cell id."""
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"{stamp}_{network_key}_{workload}_n{count}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default="zksync_sepolia")
    ap.add_argument("-w", "--workload", default="native_transfer")
    ap.add_argument("-c", "--count", type=int, default=1,
                    help="batch size (default 1)")
    ap.add_argument("--run-id", default=None,
                    help="experiment cell identifier, recorded on every row")
    ap.add_argument("--dry-run", action="store_true",
                    help="build, number and estimate the batch; broadcast nothing")
    args = ap.parse_args()

    networks = load_networks()
    if args.network not in networks:
        print(f"unknown network '{args.network}'. known: {', '.join(networks)}")
        return 2
    workloads = load_workloads()
    if args.workload not in workloads:
        print(f"unknown workload '{args.workload}'. "
              f"known: {', '.join(workloads)}")
        return 2

    net: Network = networks[args.network]
    if net.readonly:
        print(f"\n{net.display_name} is marked readonly in networks.yaml.")
        print("This is an observation-only network. Refusing to sign anything "
              "against it.\n")
        return 2

    workload = workloads[args.workload]
    account = load_account()
    run_id = args.run_id or default_run_id(net.key, workload.name, args.count)

    print(f"\nrun        {run_id}")
    print(f"network    {net.display_name}")
    print(f"workload   {workload.name}  x{args.count}")
    print(f"account    {account.address}")

    try:
        w3 = connect_verified(net)
    except ChainIdMismatch as exc:
        print(f"\nchain id mismatch: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\ncannot reach {net.rpc}: {type(exc).__name__}: {exc}")
        return 1

    try:
        batch = [
            build(w3, account, workload, net.key) for _ in range(args.count)
        ]
    except TokenNotDeployed as exc:
        print(f"\n{exc}")
        print("Deploy it: python -m bench.deploy_token "
              f"-n {net.key} --record\n")
        return 1

    # Read once, before the batch goes out. Cheap, and it is the only moment
    # at which the L1 condition for this run can be recorded truthfully.
    l1_gas_price = None
    if net.settles_on and net.settles_on in networks:
        try:
            l1_gas_price = connect_verified(networks[net.settles_on]).eth.gas_price
            print(f"L1 gas     {l1_gas_price / 1e9:.3f} gwei on {net.settles_on}")
        except Exception as exc:  # noqa: BLE001
            print(f"L1 gas     unavailable ({type(exc).__name__})")

    base = assign_nonces(w3, account, batch)
    print(f"nonces     {base}..{base + args.count - 1}")

    # Estimated once on the first transaction and reused across an identical
    # batch. Estimating all fifty would be fifty extra round trips against a
    # public endpoint for an answer that does not vary within one workload.
    try:
        gas = estimate_gas(w3, batch[0])
    except SubmissionError as exc:
        print(f"\n{exc}\n")
        return 1
    for tx in batch:
        tx["gas"] = gas
    print(f"gas        {gas:,} each at "
          f"{w3.from_wei(batch[0]['gasPrice'], 'gwei'):.4f} gwei")

    if args.dry_run:
        print(f"\nBuilt and numbered {len(batch)} transaction(s), "
              "nothing broadcast.\n")
        return 0

    # Submitted in index order even though nonces are already assigned: a gap
    # stalls everything behind it, so ordering matters (B3 step 3).
    records = []
    for tx in batch:
        try:
            records.append(
                submit(w3, account, tx, workload.name, net.key, run_id)
            )
        except SubmissionError as exc:
            records.append(
                rejected_record(exc, tx, workload.name, net.key, run_id)
            )

    for record in records:
        record["l1_gas_price_wei"] = l1_gas_price

    path = run_path(run_id)
    append(path, records)
    print(f"\nsubmitted  {len(records)} - {path.relative_to(path.parents[3])}")

    resolve_t1_batch(w3, records)

    # Rewritten rather than appended: the rows now carry t1 and outcomes.
    rewrite(path, records)

    stats = summarise(records)
    print(f"\noutcomes   " + ", ".join(
        f"{name} {count}" for name, count in sorted(stats["counts"].items())
    ))
    print(f"success    {stats['measurable']}/{stats['total']} "
          f"({stats['success_rate'] * 100:.0f}%)")

    for count, outcome, detail in group_failures(records):
        print(f"  {count:>4}x {outcome}  {detail}")

    discrepancy = verify_nonce_advance(w3, account, base, len(batch))
    if discrepancy:
        print(f"\nnonce      {discrepancy}")
        print("Re-sync before the next batch (B3 step 4).")

    first = next((r for r in records if r["hash"]), None)
    if first:
        print(f"\nexample    {net.tx_url(first['hash'])}")
    print()

    return 0 if stats["measurable"] == stats["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
