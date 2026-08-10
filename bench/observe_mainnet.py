"""Observe production rollups without spending anything (task F4).

    python -m bench.observe_mainnet                  # both mainnet rollups
    python -m bench.observe_mainnet -n zksync_mainnet
    python -m bench.observe_mainnet --samples 40

Testnet numbers are real but not representative. Proof cadence, batch intervals
and gas prices under production load are all different, and the difference is
not a detail: an optimistic testnet was expected to shorten its challenge window
and does not, while a ZK testnet proves far more eagerly than a mainnet under
real load would. Observation costs nothing, sends nothing, and grounds every
testnet figure against what actually happens.

    WHAT IS MEASURED

For a ZK rollup, batch details are readable directly, so commit cadence and the
commit-to-proof interval are observed rather than inferred.

For an OP Stack chain there is no such view, so the two cadences come from
different places: batch postings are counted by scanning the L1 for the
batcher's transactions, and output proposals from the dispute game factory's
own creation timestamps.

Every figure is reported with the range it was measured over, because a batch
interval without a sample window is not a claim anybody can check.
"""

import argparse
import datetime as dt
import statistics as st
import time

from bench.core.networks import Network, connect_verified, load_networks

#: How many recent batches or proposals to sample. Enough for a median that
#: means something, small enough to stay polite on a public endpoint.
DEFAULT_SAMPLES = 25

#: L1 blocks to scan when counting OP batch postings. About two hours.
OP_SCAN_BLOCKS = 600


def _as_int(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


def _iso(value: str | None) -> float | None:
    """zkSync returns ISO-8601 with microseconds and a Z suffix."""
    if not value:
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _spread(name: str, values: list[float], unit: str = "min") -> None:
    if not values:
        print(f"  {name:<30} no samples")
        return
    divisor = {"min": 60.0, "h": 3600.0, "s": 1.0}[unit]
    scaled = sorted(v / divisor for v in values)
    print(f"  {name:<30} median {st.median(scaled):7.2f} {unit}   "
          f"min {scaled[0]:6.2f}   max {scaled[-1]:7.2f}   n={len(scaled)}")


def observe_zk(net: Network, samples: int) -> None:
    w3 = connect_verified(net)
    latest = _as_int(
        w3.provider.make_request("zks_L1BatchNumber", []).get("result")
    )
    if latest is None:
        print("  zks_L1BatchNumber unavailable")
        return

    first = max(1, latest - samples + 1)
    print(f"  batches {first:,}..{latest:,}")

    sealed, commits, proves, gaps = [], [], [], []
    for number in range(first, latest + 1):
        details = w3.provider.make_request(
            "zks_getL1BatchDetails", [number]
        ).get("result") or {}
        sealed_at = details.get("timestamp")
        committed = _iso(details.get("committedAt"))
        proven = _iso(details.get("provenAt"))
        if sealed_at:
            sealed.append(float(sealed_at))
        if committed:
            commits.append(committed)
        if proven:
            proves.append(proven)
        if committed and proven:
            gaps.append(proven - committed)

    def deltas(series: list[float]) -> list[float]:
        series = sorted(series)
        return [b - a for a, b in zip(series, series[1:])]

    _spread("batch seal interval", deltas(sealed))
    _spread("L1 commit interval", deltas(commits))
    _spread("commit -> proof", gaps)
    covered = (max(sealed) - min(sealed)) / 3600 if len(sealed) > 1 else 0
    print(f"  window covered                 {covered:.2f} h")


def observe_op(net: Network, networks: dict, samples: int) -> None:
    from bench.adapters.optimism import _DGF_ABI, _GAME_ABI, _PORTAL_ABI

    w3_l1 = connect_verified(networks[net.settles_on])

    portal = w3_l1.eth.contract(
        address=w3_l1.to_checksum_address(net.l1_portal), abi=_PORTAL_ABI
    )
    maturity = int(portal.functions.proofMaturityDelaySeconds().call())
    finality = int(portal.functions.disputeGameFinalityDelaySeconds().call())
    print(f"  proof maturity delay           {maturity / 86400:.2f} days")
    print(f"  dispute game finality delay    {finality / 86400:.2f} days")

    factory = w3_l1.eth.contract(
        address=w3_l1.to_checksum_address(net.l1_dispute_game_factory),
        abi=_DGF_ABI,
    )
    count = factory.functions.gameCount().call()
    print(f"  dispute games created          {count:,}")

    created = []
    for index in range(max(0, count - samples), count):
        try:
            _type, timestamp, _proxy = factory.functions.gameAtIndex(index).call()
            created.append(float(timestamp))
        except Exception:  # noqa: BLE001
            continue
    created.sort()
    _spread("output proposal interval",
            [b - a for a, b in zip(created, created[1:])])

    # Batch postings, by scanning the L1 for the batcher. There is no view that
    # exposes these, so this is a count over a stated window rather than a
    # lookup - which is why the window is printed with it.
    batcher = (net.l1_batcher or "").lower()
    inbox = (net.l1_batch_inbox or "").lower()
    head = w3_l1.eth.block_number
    first = head - OP_SCAN_BLOCKS
    postings, blobs = [], 0
    for number in range(first, head + 1):
        try:
            block = w3_l1.eth.get_block(number, full_transactions=True)
        except Exception:  # noqa: BLE001
            continue
        for tx in block["transactions"]:
            if ((tx["from"] or "").lower() == batcher
                    and (tx["to"] or "").lower() == inbox):
                postings.append(float(block["timestamp"]))
                blobs += len(tx.get("blobVersionedHashes") or [])
    postings.sort()
    _spread("batch posting interval",
            [b - a for a, b in zip(postings, postings[1:])])
    span = (postings[-1] - postings[0]) / 3600 if len(postings) > 1 else 0
    print(f"  postings in L1 blocks {first:,}..{head:,}: {len(postings)} "
          f"carrying {blobs} blob(s) over {span:.2f} h")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default=None,
                    help="one readonly network instead of all of them")
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    args = ap.parse_args()

    networks = load_networks()
    keys = ([args.network] if args.network
            else [k for k, n in networks.items() if n.readonly and n.is_l2])

    print(f"\nobserved at {dt.datetime.now().isoformat(timespec='seconds')}  "
          f"(read-only; nothing is sent)")

    for key in keys:
        net = networks.get(key)
        if net is None:
            print(f"unknown network '{key}'")
            return 2
        print(f"\n{net.display_name}  (chain {net.chain_id})")
        started = time.time()
        try:
            if net.family == "zk":
                observe_zk(net, args.samples)
            else:
                observe_op(net, networks, args.samples)
        except Exception as exc:  # noqa: BLE001
            print(f"  failed: {type(exc).__name__}: {exc}")
        print(f"  took {time.time() - started:.0f}s")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
