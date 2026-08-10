"""Submission, nonce assignment, and full-trust finality (tasks B2, B3, B4, B5).

B2 is the keystone: until a real hash comes back from a real node, nothing
downstream can be built. Everything here exists to get that hash honestly and to
record enough context around it that the rest of the study has something to
attach to.

    NEVER FABRICATE A HASH

If submission fails this module raises, and the caller records the failure with
hash left as None. Returning a placeholder that later code polls for is the
single most damaging thing that can be done to a benchmark: every downstream
measurement becomes plausible-looking and meaningless, and nothing warns you.
The same rule governs t1 - a timeout is recorded as a timeout, never as a very
large latency.
"""

import time
from typing import Any, Iterable

from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxParams

from bench.core.records import Outcome, new_record

# How long to wait for an L2 receipt before calling it a failure (B4).
RECEIPT_DEADLINE_S = 120.0

# Backoff bounds for receipt polling. A tight loop would measure our own polling
# interval as much as the network, and public endpoints throttle it anyway.
#
# The cap is 2s rather than the 10s that felt natural, because t1 can never be
# finer than this interval and a lone transaction on zkSync Sepolia resolves in
# about 0.6s - a 10s cap would have quantised the fast case into meaninglessness.
#
# Measured 2026-08-10, both caps, same batch size: a batch of 50 resolved at a
# median of 18.6s at the 10s cap and 17.5s at 2s. So the batch latency is the
# sequencer working through 50 queued transactions from one account, not our
# polling schedule - worth stating plainly because the first reading of that
# number was that it must be an artefact, and it is not. That contrast between
# n=1 and n=50 is the effect F3's batch sweep exists to characterise.
#
# t1 remains an upper bound on full-trust latency, and l2_block_ts is recorded
# alongside it so the overshoot is measurable rather than assumed. Both facts
# belong in threats to validity.
POLL_INITIAL_S = 0.25
POLL_MAX_S = 2.0


class SubmissionError(RuntimeError):
    """The node refused the transaction. There is no hash, and none is invented."""


def to_hex(value: Any) -> str:
    """A 0x-prefixed hash string, whatever the client handed back.

    web3 v7 returns HexBytes whose .hex() has no 0x prefix, v6 included one, and
    a raw str may arrive either way. Normalising once here keeps explorer URLs
    and stored hashes consistent - and avoids the lstrip("0x") idiom, which
    silently eats a leading zero from a hash that begins with one.
    """
    if isinstance(value, (bytes, bytearray, HexBytes)):
        text = HexBytes(value).hex()
    else:
        text = str(value)
    text = text[2:] if text.startswith(("0x", "0X")) else text
    return "0x" + text.lower()


def estimate_gas(w3: Web3, tx: dict) -> int:
    """Gas for one transaction, estimated rather than hardcoded (B2 step 4).

    Different workloads need very different amounts, and on some rollups the
    estimate exceeds actual usage several-fold - which is fine for a limit and
    useless as a cost figure. D2 costs from receipts, never from this.
    """
    # Any gas already on the dict is stripped first. A node reads that field as
    # the allowance for the simulation, so a placeholder left there - the usual
    # trick for stopping build_transaction estimating behind your back - comes
    # back as "gas required exceeds allowance (1)". zkSync ignores the field and
    # geth does not, so this fails on one chain and not the other.
    probe = {k: v for k, v in tx.items() if k != "gas"}
    try:
        return w3.eth.estimate_gas(dict_to_tx(probe))
    except Exception as exc:  # noqa: BLE001
        raise SubmissionError(f"estimate_gas: {type(exc).__name__}: {exc}") from exc


def dict_to_tx(tx: dict) -> TxParams:
    """Our plain dicts are TxParams as far as web3 is concerned."""
    return tx  # type: ignore[return-value]


def assign_nonces(w3: Web3, account, transactions: list[dict]) -> int:
    """Number a batch consecutively from one fetched count (B3 steps 1-2).

    Fetching the nonce inside each task gives several transactions the same
    number and all but one are rejected, so the count is taken once here and
    incremented locally. Returns the base for the caller to verify against.
    """
    base = w3.eth.get_transaction_count(account.address)
    for offset, tx in enumerate(transactions):
        tx["nonce"] = base + offset
    return base


def verify_nonce_advance(w3: Web3, account, base: int, expected: int) -> str | None:
    """Confirm the account's count moved as far as the batch should have (B3 4).

    Returns a description of the discrepancy, or None if it advanced correctly.
    A short advance means a transaction was dropped rather than rejected - which
    leaves a gap that stalls everything behind it, so it has to be caught before
    the next batch rather than diagnosed later.
    """
    actual = w3.eth.get_transaction_count(account.address)
    if actual == base + expected:
        return None
    return (
        f"nonce advanced to {actual}, expected {base + expected} "
        f"({base + expected - actual} transaction(s) unaccounted for)"
    )


def submit(
    w3: Web3,
    account,
    tx: dict,
    workload: str,
    network_key: str,
    run_id: str,
) -> dict:
    """Sign, broadcast, and return a record (B2 steps 1-3).

    A record rather than a bare hash, because everything downstream needs the
    context: which workload produced it, when it went out, on which network.
    """
    if "gas" not in tx:
        tx["gas"] = estimate_gas(w3, tx)

    signed = account.sign_transaction(dict_to_tx(tx))

    # t0 is captured here, immediately before the broadcast and after every
    # local cost - signing, estimation, encoding. Every latency in the study is
    # measured from this instant, so anything expensive between it and the send
    # would be silently attributed to the network.
    t0 = time.time()
    try:
        raw = w3.eth.send_raw_transaction(signed.raw_transaction)
    except Exception as exc:  # noqa: BLE001
        raise SubmissionError(f"{type(exc).__name__}: {exc}") from exc

    return new_record(
        run_id=run_id,
        network=network_key,
        chain_id=tx.get("chainId"),
        workload=workload,
        hash=to_hex(raw),
        nonce=tx.get("nonce"),
        t0=t0,
        outcome=Outcome.SUBMITTED,
        gas_limit=tx.get("gas"),
        gas_price_wei=tx.get("gasPrice"),
    )


def rejected_record(
    exc: Exception,
    tx: dict,
    workload: str,
    network_key: str,
    run_id: str,
) -> dict:
    """A record for a transaction the node refused.

    hash stays None. A rejected transaction is a real observation and belongs in
    the output - it is what makes the success rate reportable (B5 step 3) - but
    it must never carry an invented identifier.
    """
    return new_record(
        run_id=run_id,
        network=network_key,
        chain_id=tx.get("chainId"),
        workload=workload,
        nonce=tx.get("nonce"),
        outcome=Outcome.REJECTED,
        detail=f"{type(exc).__name__}: {exc}"[:200],
        gas_limit=tx.get("gas"),
        gas_price_wei=tx.get("gasPrice"),
    )


def _try_receipt(w3: Web3, tx_hash: str):
    """The receipt, or None if it is not mined yet.

    Some web3 versions raise TransactionNotFound rather than returning None.
    Both mean "not yet"; neither is an error worth propagating.
    """
    try:
        return w3.eth.get_transaction_receipt(HexBytes(tx_hash))
    except Exception:  # noqa: BLE001
        return None


def _apply_receipt(record: dict, receipt, seen_at: float) -> None:
    """Fill in t1, gas and outcome from a receipt (B4 step 4, B5 steps 1-2)."""
    record["t1"] = seen_at
    record["gas_used"] = receipt["gasUsed"]
    record["block"] = receipt["blockNumber"]
    if receipt["status"] == 1:
        record["outcome"] = Outcome.SUCCESS
    else:
        # On-chain but failed. Excluded from latency figures, counted in the
        # success rate, never silently discarded.
        record["outcome"] = Outcome.REVERTED
        record["detail"] = "receipt status 0"


def resolve_t1(w3: Web3, record: dict, deadline_s: float = RECEIPT_DEADLINE_S) -> dict:
    """Poll one transaction for its receipt. Mutates and returns the record."""
    resolve_t1_batch(w3, [record], deadline_s)
    return record


def resolve_t1_batch(
    w3: Web3,
    records: list[dict],
    deadline_s: float = RECEIPT_DEADLINE_S,
) -> list[dict]:
    """Poll a whole batch round-robin, filling in t1 for each (B4).

    Round-robin rather than one transaction at a time, because t1 is a wall
    clock reading and polling serially would make it measure our own queue.
    Waiting out transaction 1 before first asking about transaction 50 adds that
    entire wait to transaction 50's latency, and the effect grows with batch
    size - which would show up as "larger batches finalise more slowly" and be
    entirely an artefact of the measuring instrument.

    A residual skew remains: within one cycle the last record is checked after
    the RPC round trips for all the earlier ones. That is bounded by the
    endpoint's response time rather than by the batch's own latency, and it
    belongs in threats to validity (G5).
    """
    pending = [r for r in records if r["outcome"] == Outcome.SUBMITTED and r["hash"]]
    delay, started = POLL_INITIAL_S, time.time()

    while pending and time.time() - started < deadline_s:
        still_pending = []
        for record in pending:
            receipt = _try_receipt(w3, record["hash"])
            if receipt is None:
                still_pending.append(record)
            else:
                _apply_receipt(record, receipt, time.time())
        pending = still_pending
        if pending:
            time.sleep(delay)
            delay = min(delay * 2, POLL_MAX_S)

    for record in pending:
        # A timeout is a failure with a reason, never a very large latency.
        record["outcome"] = Outcome.TIMEOUT
        record["detail"] = f"no receipt within {deadline_s:.0f}s"

    annotate_block_times(w3, records)
    return records


def annotate_block_times(w3: Web3, records: list[dict]) -> None:
    """Record each transaction's L2 block timestamp.

    One fetch per distinct block, not per transaction, because a batch often
    lands across far fewer blocks than it has transactions - and on a public
    endpoint the difference is the difference between polite and throttled.
    """
    wanted = {r["block"] for r in records if r.get("block") is not None}
    timestamps: dict[int, int] = {}
    for number in wanted:
        try:
            timestamps[number] = w3.eth.get_block(number)["timestamp"]
        except Exception:  # noqa: BLE001
            # Reported as unavailable rather than filled with a plausible
            # number. A missing field is a limitation; an invented one is not.
            continue
    for record in records:
        if record.get("block") in timestamps:
            record["l2_block_ts"] = timestamps[record["block"]]


def summarise(records: Iterable[dict]) -> dict:
    """Counts by outcome, plus the success rate (B5 step 3).

    Deliberately returns no latency statistic. Latencies are computed from
    absolute timestamps at analysis time (C5), and a summary emitted during
    collection is exactly the pre-computed duration that task forbids.
    """
    rows = list(records)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    measurable = sum(counts.get(o, 0) for o in Outcome.MEASURABLE)
    return {
        "total": len(rows),
        "counts": counts,
        "measurable": measurable,
        "success_rate": (measurable / len(rows)) if rows else 0.0,
    }


def group_failures(records: Iterable[dict]) -> list[tuple[int, str, str]]:
    """Distinct failures with their counts, most common first (B5 step 4).

    A hundred copies of one message hides the one that differs.
    """
    seen: dict[tuple[str, str], int] = {}
    for row in records:
        if row["outcome"] in Outcome.MEASURABLE:
            continue
        key = (row["outcome"], (row.get("detail") or "")[:120])
        seen[key] = seen.get(key, 0) + 1
    ordered = sorted(seen.items(), key=lambda kv: kv[1], reverse=True)
    return [(count, outcome, detail) for (outcome, detail), count in ordered]
