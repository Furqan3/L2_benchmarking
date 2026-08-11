"""Turn L1 settlement hashes into t2 and t3 (tasks C2, C3, C4, C5).

The adapter says which Ethereum transactions settled an L2 transaction. This
module turns those hashes into timestamps by fetching the L1 transaction,
reading its block number, fetching that block, and taking its timestamp.

    WHY THE TIMESTAMPS ARE ABSOLUTE AND SEPARATE

Four absolute timestamps are stored per transaction and no duration is computed
here (C5). Every latency is t_n minus t0 at analysis time. Timing each level
from the end of the previous one - or computing t3 - t2 and calling it trustless
latency - silently folds the earlier stages into the later ones and inflates
every trustless figure in the study.

    L1 BLOCK TIMESTAMPS ARE NOT EXACT

They have second granularity and are set by the proposer, so they can drift
slightly from real time. Against intervals measured in minutes and hours this is
negligible, but it belongs in threats to validity (G5) and C3 says so directly.
"""

from web3 import Web3

from bench.adapters.base import Settlement, SettlementUnavailable

#: L1 lookups are cached per process. A batch holds many of our transactions, so
#: a run of 50 typically maps onto a handful of distinct L1 transactions, and
#: re-fetching each block once per row would multiply the request count by fifty
#: for no new information - the fastest way to get rate-limited (C2 step 2).
_BLOCK_CACHE: dict[tuple[int, str], tuple[int, int]] = {}


def l1_timestamp(w3_l1: Web3, tx_hash: str) -> tuple[int, int]:
    """(block timestamp, block number) for one L1 transaction.

    Raises if the transaction cannot be found. A settlement hash that does not
    resolve is a real problem worth stopping on, not a field to leave blank.
    """
    key = (w3_l1.eth.chain_id, tx_hash.lower())
    if key in _BLOCK_CACHE:
        return _BLOCK_CACHE[key]

    tx = w3_l1.eth.get_transaction(tx_hash)
    block_number = tx["blockNumber"]
    if block_number is None:
        # In the L1 mempool but not yet mined. Genuinely "not yet", so the
        # resolve pass should ask again rather than record anything.
        raise SettlementUnavailable(f"{tx_hash} is not yet in an L1 block")

    timestamp = w3_l1.eth.get_block(block_number)["timestamp"]
    _BLOCK_CACHE[key] = (timestamp, block_number)
    return timestamp, block_number


def _plausible(record: dict, timestamp: int, field: str) -> bool:
    """Refuse a settlement timestamp that precedes the transaction itself.

    A batch posted before our transaction was broadcast cannot contain it, so
    such a value is not a slightly-wrong measurement - it is the wrong batch.

    This is not hypothetical. Matching an OP Stack batch by scanning forward
    from the L2 block's L1 origin found a posting nine seconds BEFORE t0,
    because the L1 origin lags the L1 head by a sequencer window. The scan now
    guards on time as well, and this check is the backstop: any future matching
    heuristic that makes the same class of mistake fails here rather than
    producing a negative latency that a reader has to notice by eye.
    """
    t0 = record.get("t0")
    if t0 is None or timestamp >= t0:
        return True
    print(f"  refused {field} {timestamp} for {str(record.get('hash'))[:14]}...: "
          f"{t0 - timestamp:.0f}s before t0 - wrong batch, not recorded")
    return False


def apply_settlement(
    record: dict,
    settlement: Settlement,
    w3_l1: Web3,
) -> dict:
    """Fill in t2, t3 and the L1 hashes on one record. Mutates and returns it.

    Only fields the adapter actually knows are written. A stage that has not
    happened leaves its timestamp None, which is what tells the next resolve
    pass there is still work to do.
    """
    record["settle_status"] = settlement.status
    if settlement.batch is not None:
        record["batch"] = settlement.batch
    if settlement.batch_tx_count is not None:
        record["batch_tx_count"] = settlement.batch_tx_count

    # The L1 hashes go in the output whether or not their timestamps resolve.
    # They are what make the result checkable by someone who does not trust us
    # (C2 step 4), and that value does not depend on our arithmetic.
    if settlement.commit_tx:
        record["l1_commit_tx"] = settlement.commit_tx
    if settlement.prove_tx:
        record["l1_prove_tx"] = settlement.prove_tx
    if settlement.execute_tx:
        record["l1_execute_tx"] = settlement.execute_tx

    record["t2_kind"] = settlement.t2_kind

    if settlement.commit_tx and record.get("t2") is None:
        try:
            timestamp, block = l1_timestamp(w3_l1, settlement.commit_tx)
            if _plausible(record, timestamp, "t2"):
                record["t2"], record["l1_commit_block"] = timestamp, block
        except SettlementUnavailable:
            pass

    # A derived t3 has no L1 transaction behind it - nothing happens when a
    # challenge window closes - so it is taken straight from the adapter and
    # flagged, never looked up and never presented as an observation.
    if settlement.t3_derived_at is not None and record.get("t3") is None:
        # Guarded exactly like a looked-up timestamp. Leaving this path
        # unguarded was how 196 rows acquired a trustless timestamp 209 days
        # before their own submission: the value was computed rather than
        # fetched, so it never passed through the check that would have caught
        # it. A derived number deserves more scrutiny than an observed one, not
        # less.
        if _plausible(record, settlement.t3_derived_at, "t3"):
            record["t3"] = settlement.t3_derived_at
            record["t3_kind"] = settlement.t3_kind
            record["t3_source"] = settlement.t3_source

    if settlement.prove_tx and record.get("t3") is None:
        try:
            record["t3"], record["l1_prove_block"] = l1_timestamp(
                w3_l1, settlement.prove_tx
            )
            record["t3_kind"] = settlement.t3_kind
            record["t3_source"] = settlement.t3_source
        except SettlementUnavailable:
            pass

    return record


def outstanding(record: dict) -> bool:
    """True when a resolve pass still has work to do on this row.

    Rows that never got on chain are finished regardless of their timestamps:
    a rejected transaction has no batch to wait for, and polling it forever
    would make every resolve pass look permanently incomplete.
    """
    if not record.get("hash"):
        return False
    if record.get("outcome") in ("rejected", "timeout"):
        return False
    if record.get("t2") is None or record.get("t3") is None:
        return True
    # Settled, but missing the batch identity. D2 divides a batch's L1 cost by
    # the number of transactions sharing it, so a row without batch_tx_count
    # cannot be costed at all - and a row that has every timestamp still looks
    # finished to anything that only checks timestamps.
    #
    # Not demanded of rollups that cannot supply it. An OP Stack chain exposes
    # no batch composition without decoding the blob, so requiring it there
    # would leave every optimistic row outstanding for ever and make the
    # resolve pass never finish.
    if record.get("t2_kind") == "estimated":
        return False
    return record.get("batch") is None or record.get("batch_tx_count") is None
