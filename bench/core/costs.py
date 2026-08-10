"""Real L1 cost and data-availability size (tasks D2, D3).

Cost comes from the Ethereum transactions that actually settled a batch - their
receipts' gasUsed and effectiveGasPrice - never from a constant and never from
a gas estimate. Estimates on zkSync overshoot actual usage by up to nine times,
so an estimate-derived cost would not be wrong by a little.

    THE PER-TRANSACTION SHARE IS NOT DIVIDED BY OUR OWN COUNT

A batch carries everybody's transactions. Batch 21623 held 875 and 109 of them
were ours. Dividing its L1 cost by 109 would overstate each transaction's share
eightfold. The denominator is the batch's own transaction count, which the
adapter reads from the rollup.

    BYTES AND COST ARE REPORTED SEPARATELY

The byte count is a fact about the rollup. The cost depends on price
assumptions that are ours. D3 says to keep them apart, and the two travel
through this module as separate fields for that reason.
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
PRICING_CONFIG = REPO_ROOT / "bench" / "configs" / "pricing.yaml"

#: One EIP-4844 blob, in bytes: 4096 field elements of 32 bytes each.
BLOB_BYTES = 131_072

#: Gas per non-zero calldata byte, for pre-4844 batch posting (D3 step 3).
CALLDATA_GAS_PER_NONZERO_BYTE = 16
CALLDATA_GAS_PER_ZERO_BYTE = 4

#: EIP-4844 blob-carrying transaction type.
BLOB_TX_TYPE = 3


@dataclass
class L1Cost:
    """What one L1 settlement transaction cost, and how much data it carried."""

    tx_hash: str
    tx_type: int
    block: int

    # Execution
    gas_used: int = 0
    effective_gas_price: int = 0

    # Data availability. Exactly one of these paths applies per transaction.
    blob_count: int = 0
    blob_gas_used: int = 0
    blob_gas_price: int = 0
    calldata_bytes: int = 0
    calldata_nonzero_bytes: int = 0

    #: Set when a field could not be read. Reported as unavailable rather than
    #: filled with a plausible number (D3 pitfall).
    unavailable: list[str] = field(default_factory=list)

    @property
    def execution_wei(self) -> int:
        return self.gas_used * self.effective_gas_price

    @property
    def blob_wei(self) -> int:
        return self.blob_gas_used * self.blob_gas_price

    @property
    def total_wei(self) -> int:
        return self.execution_wei + self.blob_wei

    @property
    def da_bytes(self) -> int:
        """Bytes posted to Ethereum for data availability.

        Blobs are counted at their full size because that is what is paid for:
        a blob is priced whole whether or not the rollup filled it. The unused
        remainder is a real cost of the design, not an accounting artefact.
        """
        if self.blob_count:
            return self.blob_count * BLOB_BYTES
        return self.calldata_bytes

    @property
    def da_kind(self) -> str:
        return "blob" if self.blob_count else "calldata"

    @property
    def calldata_gas(self) -> int:
        """What the calldata alone cost in gas, for a pre-4844 posting."""
        zero = self.calldata_bytes - self.calldata_nonzero_bytes
        return (self.calldata_nonzero_bytes * CALLDATA_GAS_PER_NONZERO_BYTE
                + zero * CALLDATA_GAS_PER_ZERO_BYTE)


def l1_cost(w3_l1: Web3, tx_hash: str) -> L1Cost:
    """Cost and DA size for one L1 settlement transaction, from its receipt."""
    tx = w3_l1.eth.get_transaction(tx_hash)
    receipt = w3_l1.eth.get_transaction_receipt(tx_hash)

    cost = L1Cost(
        tx_hash=tx_hash,
        tx_type=int(tx.get("type", 0)),
        block=receipt["blockNumber"],
        gas_used=receipt["gasUsed"],
        effective_gas_price=receipt.get("effectiveGasPrice", 0),
    )

    if cost.tx_type == BLOB_TX_TYPE:
        hashes = tx.get("blobVersionedHashes") or []
        cost.blob_count = len(hashes)
        # Present on the receipt for blob transactions. Absent on some nodes,
        # in which case the byte count still stands and only the price is lost.
        if "blobGasUsed" in receipt:
            cost.blob_gas_used = receipt["blobGasUsed"]
        else:
            cost.unavailable.append("blobGasUsed")
        if "blobGasPrice" in receipt:
            cost.blob_gas_price = receipt["blobGasPrice"]
        else:
            cost.unavailable.append("blobGasPrice")
    else:
        data = tx.get("input") or b""
        raw = bytes(data) if not isinstance(data, str) else bytes.fromhex(
            data.removeprefix("0x")
        )
        cost.calldata_bytes = len(raw)
        cost.calldata_nonzero_bytes = sum(1 for b in raw if b != 0)

    return cost


def pricing() -> dict:
    """The recorded ETH rate and its provenance (D2 step 2)."""
    cfg = yaml.safe_load(PRICING_CONFIG.read_text()) or {}
    return cfg.get("eth_usd") or {}


def to_usd(wei: int, eth_usd: float) -> float:
    """Wei to dollars at a stated rate. The rate is always passed in, never
    read from global state, so no figure can be produced without one."""
    return float(Web3.from_wei(wei, "ether")) * eth_usd
