"""Compile and deploy the workload ERC-20 (task B1 step 2).

    python -m bench.deploy_token -n zksync_sepolia --dry-run   # estimate only
    python -m bench.deploy_token -n zksync_sepolia             # deploy
    python -m bench.deploy_token -n zksync_sepolia --record    # deploy and
                                                               # write the
                                                               # address to
                                                               # workloads.yaml

Compiled from bench/contracts/BenchToken.sol against the OpenZeppelin sources
vendored at 5.0.2, with a pinned solc. Deploying once per network and recording
the address is what makes the erc20_transfer workload available there.

    A NOTE ON zkSync

zkSync Era runs its own VM. Historically it required contracts to be compiled
with zksolc rather than solc, and EVM bytecode could not be deployed at all.
More recent versions added an EVM bytecode interpreter. Rather than assume
either way, --dry-run asks the node: if the estimate succeeds, EVM bytecode is
accepted on that network, and if it reverts, this network needs the zksolc
toolchain and that is a finding worth recording in the implementation section.
"""

import argparse
import re
from pathlib import Path

import solcx
from web3 import Web3

from bench.core.networks import (
    ChainIdMismatch,
    Network,
    connect_verified,
    load_networks,
)
from bench.core.submit import SubmissionError, estimate_gas
from bench.core.wallet import load_account
from bench.core.workloads import WORKLOADS_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACTS_DIR = REPO_ROOT / "bench" / "contracts"
SOURCE = CONTRACTS_DIR / "BenchToken.sol"
CONTRACT_NAME = "BenchToken"

# Pinned. A different compiler version produces different bytecode, which would
# make the deployed contract not the one this repository describes.
SOLC_VERSION = "0.8.24"
OPTIMIZER_RUNS = 200

RECEIPT_DEADLINE_S = 300.0


def compile_token() -> tuple[list, str]:
    """(abi, bytecode) for BenchToken, compiled from committed sources."""
    solcx.install_solc(SOLC_VERSION)
    compiled = solcx.compile_files(
        [SOURCE],
        output_values=["abi", "bin"],
        solc_version=SOLC_VERSION,
        base_path=str(CONTRACTS_DIR),
        allow_paths=str(CONTRACTS_DIR),
        optimize=True,
        optimize_runs=OPTIMIZER_RUNS,
    )
    for key, artefact in compiled.items():
        if key.endswith(f":{CONTRACT_NAME}"):
            return artefact["abi"], artefact["bin"]
    raise RuntimeError(f"{CONTRACT_NAME} not found in compiler output")


def record_address(network_key: str, address: str) -> bool:
    """Write one token address into workloads.yaml, in place.

    A targeted line edit rather than a YAML round-trip: safe_dump would discard
    every comment in that file, and the comments are the part explaining why the
    recipient is what it is.
    """
    text = WORKLOADS_CONFIG.read_text()
    pattern = re.compile(rf"^(\s*{re.escape(network_key)}:).*$", re.MULTILINE)
    if not pattern.search(text):
        return False
    WORKLOADS_CONFIG.write_text(pattern.sub(rf'\1 "{address}"', text, count=1))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default="zksync_sepolia",
                    help="network key from networks.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="estimate deployment gas, do not broadcast")
    ap.add_argument("--record", action="store_true",
                    help="write the deployed address into workloads.yaml")
    args = ap.parse_args()

    networks = load_networks()
    if args.network not in networks:
        print(f"unknown network '{args.network}'. known: {', '.join(networks)}")
        return 2
    net: Network = networks[args.network]
    if net.readonly:
        print(f"\n{net.display_name} is marked readonly in networks.yaml.")
        print("This is an observation-only network. Refusing to sign anything "
              "against it.\n")
        return 2

    account = load_account()

    abi, bytecode = compile_token()
    print(f"\ncontract   {CONTRACT_NAME}  (solc {SOLC_VERSION}, "
          f"optimizer {OPTIMIZER_RUNS} runs)")
    print(f"bytecode   {len(bytecode) // 2:,} bytes")
    print(f"network    {net.display_name}")
    print(f"deployer   {account.address}")

    try:
        w3 = connect_verified(net)
    except ChainIdMismatch as exc:
        print(f"\nchain id mismatch: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"\ncannot reach {net.rpc}: {type(exc).__name__}: {exc}")
        return 1

    contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx = contract.constructor().build_transaction({
        "from": account.address,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
        # Placeholder so build_transaction does not estimate behind our back;
        # replaced below so an estimation failure is reported as one.
        "gas": 1,
    })

    try:
        tx["gas"] = estimate_gas(w3, tx)
    except SubmissionError as exc:
        print(f"\n{exc}")
        print(f"\n{net.display_name} will not accept this bytecode. A rollup "
              "running its own VM - zkSync historically - needs its own "
              "compiler; record that and deploy where it is accepted.\n")
        return 1

    fee_wei = tx["gas"] * tx["gasPrice"]
    balance = w3.eth.get_balance(account.address)
    print(f"gas        {tx['gas']:,} at "
          f"{w3.from_wei(tx['gasPrice'], 'gwei'):.4f} gwei")
    print(f"cost       {w3.from_wei(fee_wei, 'ether'):.8f} ETH "
          f"of {w3.from_wei(balance, 'ether'):.6f} available")

    if args.dry_run:
        print("\nEstimate succeeded, so this network accepts the bytecode. "
              "Drop --dry-run to deploy.\n")
        return 0

    if balance < fee_wei:
        print("\ninsufficient funds to deploy\n")
        return 1

    signed = account.sign_transaction(tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\nsent       {tx_hash.hex()}")

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=RECEIPT_DEADLINE_S)
    if receipt["status"] != 1:
        print("deployment REVERTED\n")
        return 1

    address = Web3.to_checksum_address(receipt["contractAddress"])
    print(f"address    {address}")
    print(f"explorer   {net.address_url(address)}")
    print(f"gas used   {receipt['gasUsed']:,}")

    if args.record:
        if record_address(net.key, address):
            print(f"\nrecorded in {WORKLOADS_CONFIG.name} under tokens.{net.key}")
        else:
            print(f"\ncould not find 'tokens.{net.key}' in "
                  f"{WORKLOADS_CONFIG.name} - add it by hand")
    else:
        print(f"\nNot recorded. Add to {WORKLOADS_CONFIG.name}:  "
              f"{net.key}: \"{address}\"")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
