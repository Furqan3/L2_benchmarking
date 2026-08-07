"""Send one transaction and time its inclusion (tasks A5, and the seed of B2/B4).

A5 exists to prove that key, endpoint, chain id, nonce, gas and signing all
work together, before any framework is built on top of them. When something
breaks in B2, this script is the reference that says the rest is fine.

It sends to our own address with zero value, so nothing is spent but gas.

    python -m bench.send_one --dry-run          # build and sign, do not broadcast
    python -m bench.send_one                    # broadcast on Sepolia
    python -m bench.send_one -n zksync_sepolia  # once the bridge has landed

--dry-run needs no funds at all: signing happens locally. It is the fastest
way to confirm the whole path is sound while waiting on a faucet or a bridge.
"""

import argparse
import time

from web3 import Web3

from bench.core.networks import ChainIdMismatch, connect_verified, load_networks
from bench.core.wallet import load_account

# A plain self-transfer. Kept as a constant because B2 will need the same
# floor when gas estimation is unavailable on an unfunded account.
PLAIN_TRANSFER_GAS = 21_000

# How long to wait for the receipt before calling it a failure. A timeout is
# recorded as a failure with a reason, never as a very large latency.
RECEIPT_DEADLINE_S = 180.0


def build(w3: Web3, account, value_wei: int = 0) -> dict:
    """A signed-ready transaction dict, sending to ourselves."""
    tx = {
        "from": account.address,
        "to": account.address,
        "value": value_wei,
        "nonce": w3.eth.get_transaction_count(account.address),
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
    }
    try:
        tx["gas"] = w3.eth.estimate_gas(tx)
    except Exception:
        # An empty account cannot be estimated against on some nodes, because
        # the node simulates the transaction and sees it cannot pay. The floor
        # for a plain transfer is fixed and known, so fall back to it.
        tx["gas"] = PLAIN_TRANSFER_GAS
    return tx


def wait_for_receipt(w3: Web3, tx_hash, deadline_s: float = RECEIPT_DEADLINE_S):
    """Poll until the receipt appears. Returns (arrival_time, receipt).

    Exponential backoff rather than a tight loop: public endpoints rate-limit,
    and a tight loop measures our own polling interval as much as the network.
    This is the logic B4 will reuse for full-trust finality.
    """
    delay, started = 1.0, time.time()
    while time.time() - started < deadline_s:
        try:
            receipt = w3.eth.get_transaction_receipt(tx_hash)
        except Exception:
            receipt = None  # not found yet; some versions raise instead
        if receipt is not None:
            return time.time(), receipt
        time.sleep(delay)
        delay = min(delay * 2, 10.0)
    raise TimeoutError(
        f"no receipt within {deadline_s:.0f}s for {tx_hash.hex() if hasattr(tx_hash,'hex') else tx_hash}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-n", "--network", default="sepolia",
                    help="network key from networks.yaml (default: sepolia)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build and sign locally, but do not broadcast")
    ap.add_argument("--value-wei", type=int, default=0,
                    help="value to send to ourselves (default 0 - only gas is spent)")
    args = ap.parse_args()

    networks = load_networks()
    if args.network not in networks:
        print(f"unknown network '{args.network}'. known: {', '.join(networks)}")
        return 2
    net = networks[args.network]
    account = load_account()

    print(f"\nnetwork  {net.display_name}")
    print(f"account  {account.address}")

    try:
        w3 = connect_verified(net)
    except ChainIdMismatch as exc:
        print(f"\nchain id mismatch: {exc}")
        return 1
    except Exception as exc:
        print(f"\ncannot reach {net.rpc}: {type(exc).__name__}: {exc}")
        return 1

    balance = w3.eth.get_balance(account.address)
    tx = build(w3, account, args.value_wei)
    fee_wei = tx["gas"] * tx["gasPrice"]

    print(f"balance  {w3.from_wei(balance, 'ether'):.6f} ETH")
    print(f"nonce    {tx['nonce']}")
    print(f"gas      {tx['gas']:,} at {w3.from_wei(tx['gasPrice'], 'gwei'):.4f} gwei")
    print(f"max fee  {w3.from_wei(fee_wei, 'ether'):.8f} ETH")

    signed = account.sign_transaction(tx)

    if args.dry_run:
        raw = signed.raw_transaction
        print(f"\nsigned locally, {len(raw)} bytes - not broadcast")
        print(f"raw      {raw.hex()[:80]}...")
        print("\nEverything up to broadcast works. Drop --dry-run to send.\n")
        return 0

    if balance < fee_wei:
        print(f"\ninsufficient funds: need {w3.from_wei(fee_wei, 'ether'):.8f} ETH for gas")
        print("Fund this network first, or use --dry-run.\n")
        return 1

    t0 = time.time()
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    print(f"\nsent     {tx_hash.hex()}")
    print(f"explorer {net.explorer.rstrip('/')}/tx/0x{tx_hash.hex().lstrip('0x')}")

    try:
        t1, receipt = wait_for_receipt(w3, tx_hash)
    except TimeoutError as exc:
        print(f"\n{exc}")
        return 1

    status = "success" if receipt["status"] == 1 else "REVERTED"
    print(f"\nblock    {receipt['blockNumber']:,}")
    print(f"gas used {receipt['gasUsed']:,}")
    print(f"status   {status}")
    print(f"latency  {t1 - t0:.2f} s   (t1 - t0, inclusion on this chain)\n")

    return 0 if receipt["status"] == 1 else 1


if __name__ == "__main__":
    raise SystemExit(main())
