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
from bench.core.wallet import ACCOUNTS_CONFIG, check_address

# Enough to bridge and run a few hundred transactions. Below this, a run may
# die partway through, which wastes the wall-clock time it had already spent.
COMFORTABLE_ETH = Decimal("0.02")


class Status:
    OK = "funded"
    LOW = "low"
    EMPTY = "empty"
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
        "detail": "",
    }
    try:
        w3 = connect(network)
        result["chain_id"] = w3.eth.chain_id
        if result["chain_id"] != network.chain_id:
            result["status"] = Status.WRONG_CHAIN
            result["detail"] = f"expected {network.chain_id}"
            return result
        result["block"] = w3.eth.block_number
        wei = w3.eth.get_balance(Web3.to_checksum_address(address))
        eth = Decimal(str(Web3.from_wei(wei, "ether")))
        result["balance_eth"] = eth
        if eth == 0:
            result["status"] = Status.EMPTY
        elif eth < COMFORTABLE_ETH:
            result["status"] = Status.LOW
        else:
            result["status"] = Status.OK
    except Exception as exc:  # noqa: BLE001 - any failure is just "unreachable"
        result["detail"] = f"{type(exc).__name__}: {exc}"[:70]
    return result


def render(results: list[dict], address: str) -> None:
    print(f"\naccount  {address}\n")
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
        if r["status"] == Status.WRONG_CHAIN:
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
            print(f"      requires: {e.get('requires', 'unknown')}")
    print()


def record(results: list[dict]) -> None:
    """Write observed balances into accounts.yaml.

    The report's experimental setup section has to state which networks were
    used and how the account was funded. Recorded at the time, not remembered
    in week seven.
    """
    cfg = yaml.safe_load(ACCOUNTS_CONFIG.read_text()) or {}
    entries = []
    for r in results:
        if r["balance_eth"] is None:
            continue
        n: Network = r["network"]
        entries.append(
            {
                "key": n.key,
                "display_name": n.display_name,
                "chain_id": n.chain_id,
                "balance_eth": float(r["balance_eth"]),
                "checked": dt.date.today().isoformat(),
                "faucet": "TODO - record which faucet or bridge paid this",
            }
        )
    cfg["networks"] = entries
    ACCOUNTS_CONFIG.write_text(yaml.safe_dump(cfg, sort_keys=False))
    print(f"recorded {len(entries)} network(s) in {ACCOUNTS_CONFIG}")
    print("Fill in the 'faucet' field by hand - it belongs in the report.\n")


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
