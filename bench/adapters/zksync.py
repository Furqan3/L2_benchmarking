"""zkSync Era settlement lookup (task C2, preferred route).

    THE TRANSACTION ENDPOINT IS NOT ENOUGH

zks_getTransactionDetails takes an L2 hash and returns ethCommitTxHash,
ethProveTxHash and ethExecuteTxHash, which is the shortcut the handbook
describes, and it is where this adapter started.

Measured 2026-08-10: for batch 21623 the transaction endpoint returned
ethProveTxHash null while zks_getL1BatchDetails for that same batch already
carried a proveTxHash and an executeTxHash, timestamped seven minutes after the
commit. The per-transaction view lags the per-batch view, so trusting it would
have recorded t3 as "not yet" for transactions that were demonstrably proven -
a systematic undercount of trustless finality that nothing would have flagged.

So settlement is read per batch. The route is:

    L2 receipt -> l1BatchNumber -> zks_getL1BatchDetails -> L1 hashes

which is one extra call per transaction and none per batch, because batch
details are cached. A run of fifty transactions typically occupies one batch.

    WHAT COMES FREE

The batch view also carries l1TxCount and l2TxCount, which is the denominator
D2 needs to divide a batch's L1 cost by the number of transactions sharing it.
Ours are a minority of that count - batch 21623 held 874 L2 transactions and
109 of them were ours - and any per-transaction cost that ignored the other
765 would be wrong by almost an order of magnitude.

    WHICH HASH IS t3

zkSync separates proving from executing. Proof verification is the moment the
L1 can no longer be persuaded the state is wrong, so proveTxHash is t3 and
t3_source records that. executeTxHash is kept too, because C4 step 3 asks that
the distinction be recorded rather than collapsed.
"""

from bench.adapters.base import OBSERVED, Settlement, SettlementUnavailable

BATCH_METHOD = "zks_getL1BatchDetails"

#: Batch details keyed by (chain id, batch number). Many transactions share one
#: batch, so this turns an O(transactions) query pattern into O(batches) - the
#: difference between polite and rate-limited (C2 step 2).
_BATCH_CACHE: dict[tuple[int, int], dict] = {}


def _clean(value) -> str | None:
    """A hash, or None for the nulls this API returns before each stage.

    Also rejects the all-zero hash. Some rollup APIs use it as a placeholder
    for "not yet", and it would otherwise be stored as though it were a real L1
    transaction and fail to resolve in an explorer much later.
    """
    if not value or not isinstance(value, str):
        return None
    if set(value.lower().removeprefix("0x")) == {"0"}:
        return None
    return value


def _as_int(value) -> int | None:
    """RPC integers arrive as hex strings or ints depending on the field."""
    if value is None:
        return None
    if isinstance(value, str):
        return int(value, 16) if value.startswith("0x") else int(value)
    return int(value)


class ZkSyncAdapter:
    """Settlement via zkSync's L1 batch details."""

    family = "zk"

    def batch_details(self, w3_l2, batch_number: int) -> dict:
        """Cached zks_getL1BatchDetails for one batch."""
        key = (w3_l2.eth.chain_id, batch_number)
        if key in _BATCH_CACHE:
            return _BATCH_CACHE[key]

        response = w3_l2.provider.make_request(BATCH_METHOD, [batch_number])
        error = response.get("error")
        if error:
            raise SettlementUnavailable(
                f"{BATCH_METHOD}({batch_number}): {error.get('message', error)}"
            )
        details = response.get("result") or {}

        # Only cached once the batch has stopped changing. Caching a batch that
        # is committed but not yet proven would pin "no t3" for the rest of the
        # process and make every later resolve pass a no-op.
        if _clean(details.get("proveTxHash")):
            _BATCH_CACHE[key] = details
        return details

    def settlement(self, w3_l2, w3_l1, network, tx_hash: str) -> Settlement:
        # w3_l1 and network are unused here: zkSync answers from its own
        # RPC. They are in the signature so every adapter shares one.
        try:
            receipt = w3_l2.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:  # noqa: BLE001
            raise SettlementUnavailable(
                f"no L2 receipt for {tx_hash}: {type(exc).__name__}"
            ) from exc

        batch_number = _as_int(receipt.get("l1BatchNumber"))
        if batch_number is None:
            # Mined but not yet assigned to a batch. Genuinely "not yet".
            return Settlement()

        details = self.batch_details(w3_l2, batch_number)
        prove = _clean(details.get("proveTxHash"))

        l1_count = _as_int(details.get("l1TxCount")) or 0
        l2_count = _as_int(details.get("l2TxCount")) or 0

        return Settlement(
            commit_tx=_clean(details.get("commitTxHash")),
            prove_tx=prove,
            execute_tx=_clean(details.get("executeTxHash")),
            batch=batch_number,
            batch_tx_count=(l1_count + l2_count) or None,
            status=details.get("status"),
            t3_kind=OBSERVED,
            t3_source="prove" if prove else None,
        )
