"""Dry-run every workload against a live node (task B1's done-when).

B1 is finished when both workloads produce transaction dicts that a gas estimate
accepts. This script is that test. It builds each workload, shows the calldata it
would send, and asks the node to estimate it - without broadcasting anything.

    python -m bench.check_workloads                     # Sepolia
    python -m bench.check_workloads -n zksync_sepolia   # the primary target
    python -m bench.check_workloads --all               # every funded network

Estimation is a real simulation against real state, so it catches a corrupt
selector, a wrong token address and an unfunded account - which is exactly why
it is the done-when rather than a local encoding check.

Exit status is 0 when every available workload estimates, 1 otherwise.
"""

import argparse
from typing import cast

from web3 import Web3
from web3.types import TxParams

from bench.core.networks import (
    ChainIdMismatch,
    Network,
    connect_verified,
    funding_priority,
    load_networks,
)
from bench.core.wallet import load_account
from bench.core.workloads import (
    ERC20,
    TokenNotDeployed,
    Workload,
    build,
    load_abi,
    load_workloads,
    recipient,
    selector,
    signature,
    token_address,
)


class Result:
    OK = "ok"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


def describe(workload: Workload) -> None:
    """Show what this workload will actually put on the wire."""
    print(f"\n  {workload.name}  -  {workload.description}")
    if workload.kind == ERC20 and workload.abi:
        abi = load_abi(workload.abi)
        # Both derived from the committed ABI. If either looks wrong, the ABI is
        # wrong - there is nowhere else for a mistake to hide.
        print(f"    signature  {signature(abi, 'transfer')}")
        print(f"    selector   {selector(abi, 'transfer')}")


def check(w3: Web3, account, workload: Workload, network: Network) -> dict:
    """Build and estimate one workload. Never raises."""
    out = {"workload": workload, "status": Result.FAILED, "gas": None, "detail": ""}

    try:
        tx = build(w3, account, workload, network.key)
    except TokenNotDeployed as exc:
        out["status"] = Result.UNAVAILABLE
        out["detail"] = str(exc)
        return out
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"build: {type(exc).__name__}: {exc}"[:90]
        return out

    data = tx.get("data")
    if data:
        # Two calldata lengths that differ between workloads is what makes the
        # D3 done-when (DA bytes differ by workload) reachable at all.
        raw = data[2:] if data.startswith("0x") else data
        print(f"    calldata   {len(raw) // 2} bytes  {data[:26]}...")

    try:
        out["gas"] = w3.eth.estimate_gas(cast(TxParams, tx))
        out["status"] = Result.OK
    except Exception as exc:  # noqa: BLE001
        out["detail"] = f"estimate_gas: {type(exc).__name__}: {exc}"[:90]
        return out

    fee_wei = out["gas"] * tx["gasPrice"]
    print(f"    gas        {out['gas']:,} at "
          f"{w3.from_wei(tx['gasPrice'], 'gwei'):.4f} gwei")
    print(f"    fee        {w3.from_wei(fee_wei, 'ether'):.8f} ETH")
    return out


def run_network(key: str, networks: dict[str, Network], account) -> int:
    """Check every workload on one network. Returns a count of failures."""
    net = networks[key]
    print(f"\n{net.display_name}")

    try:
        w3 = connect_verified(net)
    except ChainIdMismatch as exc:
        print(f"  chain id mismatch: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"  cannot reach {net.rpc}: {type(exc).__name__}: {exc}")
        return 1

    balance = w3.eth.get_balance(account.address)
    token = token_address(key)
    print(f"  balance    {w3.from_wei(balance, 'ether'):.6f} ETH")
    print(f"  token      {token or 'not deployed (B1 step 2)'}")

    problems = 0
    for workload in load_workloads().values():
        describe(workload)
        result = check(w3, account, workload, net)
        if result["status"] == Result.OK:
            print("    status     ok")
        else:
            print(f"    status     {result['status']}  -  {result['detail']}")
            problems += 1
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default="sepolia",
                    help="network key from networks.yaml (default: sepolia)")
    ap.add_argument("--all", action="store_true",
                    help="check every network in funding_priority instead")
    args = ap.parse_args()

    networks = load_networks()
    if args.all:
        # L2s only. Workloads are submitted to rollups; the L1 is where funds
        # arrive and where t2 and t3 are read from, and it never receives a
        # workload - so including it would report a missing token there as a
        # failure forever.
        keys = [k for k in funding_priority()
                if k in networks and networks[k].is_l2]
    else:
        keys = [args.network]

    unknown = [k for k in keys if k not in networks]
    if unknown:
        print(f"unknown network(s): {', '.join(unknown)}")
        print(f"known: {', '.join(networks)}")
        return 2

    account = load_account()
    print(f"\naccount    {account.address}")
    print(f"recipient  {recipient()}")

    problems = sum(run_network(k, networks, account) for k in keys)

    if problems:
        print(f"\n{problems} workload(s) not ready.")
        print("A workload reported unavailable needs its ERC-20 deployed and "
              "recorded in workloads.yaml.\n")
        return 1

    print("\nEvery workload estimates. B1 done-when satisfied.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
