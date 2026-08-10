"""Report connectivity and balance for every network (tasks A3, A4).

This is the done-when test for A3. Run it after each faucet claim and after
each bridge; it answers "where do I actually have money" without opening a
wallet UI, and it doubles as the endpoint check A4 asks for.

    python -m bench.check_funds              # everything in the config
    python -m bench.check_funds --priority   # only what this study needs
    python -m bench.check_funds --record     # write balances to accounts.yaml

Exit status is 0 when every network the study requires is funded, 1 otherwise,
so it can gate a later script rather than needing to be read by eye.
"""

import argparse
import datetime as dt
import sys
import time
from decimal import Decimal

import yaml
from web3 import Web3

from bench.core.networks import (
    Network,
    connect,
    faucets,
    funding_priority,
    load_networks,
)
from bench.core.wallet import ACCOUNTS_CONFIG, account_created, check_address

# Enough to bridge and run a few hundred transactions, with headroom.
#
# Measured 2026-08-07 against live gas: Sepolia at 1.08 gwei made a bridge
# deposit about 0.0002 ETH, and zkSync Sepolia at 0.025 gwei made a transfer
# about 0.0000005 ETH - so the full ~550-transaction matrix costs on the order
# of 0.001 ETH. This threshold keeps roughly 5x headroom over that, which also
# covers gas spiking by an order of magnitude mid-study.
COMFORTABLE_ETH = Decimal("0.005")

# How far behind the wall clock an endpoint's latest block may be before we stop
# believing anything it says.
#
# A stalled endpoint is worse than an unreachable one: it answers, reports the
# right chain id, and serves state frozen at some point in the past - so a
# balance reads as zero and a receipt poll never resolves, both of which look
# exactly like ordinary results. Measured 2026-08-10, the public Cardona
# endpoint was serving blocks 38 days old while reporting chain 2442 correctly.
#
# Generous enough not to fire on slow L1 block times or a brief lag; anything
# beyond it is a stall, not a delay.
STALE_AFTER_S = 30 * 60


class Status:
    OK = "funded"
    LOW = "low"
    EMPTY = "empty"
    STALE = "stale"
    WRONG_CHAIN = "wrong chain"
    UNREACHABLE = "unreachable"


def probe(network: Network, address: str) -> dict:
    """Connect to one network and report what we find. Never raises."""
    result = {
        "network": network,
        "status": Status.UNREACHABLE,
        "balance_eth": None,
        "chain_id": None,
        "block": None,
        "lag_s": None,
        "detail": "",
    }
    try:
        w3 = connect(network)
        result["chain_id"] = w3.eth.chain_id
        if result["chain_id"] != network.chain_id:
            result["status"] = Status.WRONG_CHAIN
            result["detail"] = f"expected {network.chain_id}"
            return result
        # The full block, not just the number, so we can tell a live chain from
        # one whose endpoint stopped following it.
        head = w3.eth.get_block("latest")
        result["block"] = head["number"]
        result["lag_s"] = max(0.0, time.time() - head["timestamp"])

        wei = w3.eth.get_balance(Web3.to_checksum_address(address))
        eth = Decimal(str(Web3.from_wei(wei, "ether")))
        result["balance_eth"] = eth

        if result["lag_s"] > STALE_AFTER_S:
            # Deliberately overrides the funding status. The balance came from
            # stale state, so reporting it as "funded" would be a number we do
            # not actually believe.
            result["status"] = Status.STALE
            result["detail"] = f"head is {result['lag_s'] / 3600:.0f}h old"
        elif eth == 0:
            result["status"] = Status.EMPTY
        elif eth < COMFORTABLE_ETH:
            result["status"] = Status.LOW
        else:
            result["status"] = Status.OK
    except Exception as exc:  # noqa: BLE001 - any failure is just "unreachable"
        result["detail"] = f"{type(exc).__name__}: {exc}"[:70]
    return result


def render(results: list[dict], address: str) -> None:
    created = account_created()
    print(f"\naccount  {address}" + (f"   created {created}" if created else "") + "\n")
    head = f"{'network':<26}{'chain':>11}{'block':>15}{'balance (ETH)':>17}  status"
    print(head)
    print("-" * (len(head) + 6))
    for r in results:
        n: Network = r["network"]
        chain = str(r["chain_id"]) if r["chain_id"] is not None else "-"
        block = f"{r['block']:,}" if r["block"] is not None else "-"
        bal = f"{r['balance_eth']:.6f}" if r["balance_eth"] is not None else "-"
        line = f"{n.display_name:<26}{chain:>11}{block:>15}{bal:>17}  {r['status']}"
        if r["detail"]:
            line += f"  ({r['detail']})"
        print(line)
    print()


def next_actions(results: list[dict], required: list[str]) -> list[str]:
    """What to do next, based on what is actually missing."""
    by_key = {r["network"].key: r for r in results}
    todo: list[str] = []

    l1 = by_key.get("sepolia")
    if l1 and l1["status"] in (Status.EMPTY, Status.LOW):
        todo.append(
            "Claim Sepolia ETH. Start a PoW faucet mining AND submit the Google "
            "Cloud faucet at the same time - do not wait for one before the other."
        )

    for key in required:
        r = by_key.get(key)
        if not r or not r["network"].is_l2:
            continue
        if r["status"] in (Status.EMPTY, Status.LOW):
            if l1 and l1["status"] == Status.OK:
                todo.append(
                    f"Bridge Sepolia ETH to {r['network'].display_name}: "
                    f"{r['network'].bridge}  (allow ~15 minutes)"
                )
            else:
                todo.append(
                    f"{r['network'].display_name} needs funds, but fund Sepolia first."
                )

    for r in results:
        if r["status"] == Status.STALE:
            todo.append(
                f"{r['network'].key}: endpoint is serving state "
                f"{r['lag_s'] / 3600:.0f}h old. Replace it - export "
                f"BENCH_RPC_{r['network'].key.upper()}=<url> - or drop the "
                "network. Every measurement taken through it would be wrong."
            )
        elif r["status"] == Status.WRONG_CHAIN:
            todo.append(
                f"{r['network'].key}: endpoint reports chain {r['chain_id']}, "
                f"config expects {r['network'].chain_id}. Fix networks.yaml."
            )
        elif r["status"] == Status.UNREACHABLE and r["network"].key in required:
            todo.append(
                f"{r['network'].key}: endpoint unreachable. Try another RPC URL."
            )

    return todo


def print_faucets() -> None:
    tiers = faucets()
    order = [
        ("no_gatekeeping", "No gatekeeping - try these first"),
        ("free_account", "Free account required"),
        ("needs_mainnet_balance", "Needs mainnet ETH - skip if you have none"),
    ]
    print("faucets")
    for key, label in order:
        entries = tiers.get(key) or []
        if not entries:
            continue
        print(f"\n  {label}")
        for e in entries:
            print(f"    {e['name']}")
            print(f"      {e['url']}")
            print(f"      pays:     {e.get('pays', 'unknown')}")
            print(f"      requires: {e.get('requires', 'unknown')}")
    print()


def record(results: list[dict]) -> None:
    """Write observed balances into accounts.yaml.

    The report's experimental setup section has to state which networks were
    used and how the account was funded. Recorded at the time, not remembered
    in week seven.
    """
    text = ACCOUNTS_CONFIG.read_text()
    cfg = yaml.safe_load(text) or {}
    account = cfg.setdefault("account", {})

    # Keyed by network so re-running updates a row rather than appending a
    # duplicate, and so a 'faucet' field already filled in by hand survives.
    # Losing that on every re-check would be losing the only provenance record
    # the methodology section has.
    existing = {e["key"]: e for e in (account.get("networks") or [])}

    for r in results:
        if r["balance_eth"] is None:
            continue
        n: Network = r["network"]
        entry = existing.get(n.key, {"faucet": "TODO - which faucet or bridge paid this"})
        entry.update(
            {
                "key": n.key,
                "display_name": n.display_name,
                "chain_id": n.chain_id,
                "balance_eth": float(r["balance_eth"]),
                "checked": dt.date.today().isoformat(),
            }
        )
        existing[n.key] = entry

    # Nested under 'account', which is where the committed template puts it.
    # This previously wrote a second top-level 'networks' key, so the balances
    # landed somewhere nothing read them and account.networks stayed [].
    account["networks"] = list(existing.values())

    # Keep the file's leading comment block: safe_dump discards every comment,
    # and those lines are what say this account must never hold real funds.
    header = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break

    body = yaml.safe_dump(cfg, sort_keys=False)
    ACCOUNTS_CONFIG.write_text("\n".join(header + [body.rstrip()]) + "\n")

    print(f"recorded {len(existing)} network(s) in {ACCOUNTS_CONFIG.name}")
    todo = [e["key"] for e in existing.values() if str(e.get("faucet", "")).startswith("TODO")]
    if todo:
        print(f"Fill in 'faucet' by hand for: {', '.join(todo)} - it belongs "
              "in the report's experimental setup.\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--priority", action="store_true",
                    help="only the networks this study requires")
    ap.add_argument("--record", action="store_true",
                    help="write balances into accounts.yaml")
    ap.add_argument("--faucets", action="store_true",
                    help="list faucet options and exit")
    args = ap.parse_args()

    if args.faucets:
        print_faucets()
        return 0

    address = check_address()
    networks = load_networks()
    required = funding_priority()

    keys = required if args.priority else list(networks)
    results = [probe(networks[k], address) for k in keys if k in networks]

    render(results, address)

    if args.record:
        record(results)

    todo = next_actions(results, required)
    if todo:
        print("next")
        for i, t in enumerate(todo, 1):
            print(f"  {i}. {t}")
        print()

    unfunded = [
        r for r in results
        if r["network"].key in required and r["status"] != Status.OK
    ]
    return 1 if unfunded else 0


if __name__ == "__main__":
    raise SystemExit(main())
