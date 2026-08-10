"""Per-transaction records and their file format (tasks B2, C5, C6, D1).

One JSON object per line, one line per transaction. JSON Lines rather than a
single JSON array because the submit pass appends and then exits while the
resolve pass rewrites in place, possibly hours later: an interrupted append
leaves a file that is still readable, and a half-written array does not.

    THE SCHEMA IS ABSOLUTE TIMESTAMPS, NEVER DURATIONS

C5 is explicit about this. Four absolute timestamps go in the file - t0, t1, t2,
t3 - and every latency is computed from t0 at analysis time. Storing t1-t0 here
would mean an error in the arithmetic is unrecoverable, and timing each level
from the end of the previous one silently inflates every trustless figure.

    FIELDS

    run_id        experiment cell identifier (F1) - every row carries one
    network       network key from networks.yaml
    chain_id      what the endpoint reported at submission time
    workload      workload name from workloads.yaml
    hash          L2 transaction hash, 0x-prefixed. Never a placeholder.
    nonce         the nonce actually assigned
    t0            wall clock immediately before broadcast (B2)
    t1            wall clock when the L2 receipt first appeared (B4)
    l2_block_ts   the L2 block's own timestamp, for the block t1 was seen in.
                  Not a substitute for t1 - it is the sequencer's claim rather
                  than our observation - but t1 minus this is how much of the
                  measured full-trust latency is our polling interval. Without
                  it the polling overhead is invisible and t1 looks exact.
    t2            L1 block timestamp of the batch commit (C3)
    t3            L1 block timestamp of the proof, or a derived deadline (C4/E2)
    t3_kind       "observed" or "derived" - E2 requires these be distinguished
    t3_source     which event t3 came from: "prove", "execute", or
                  "challenge_window". C4 step 3 asks that proving and executing
                  not be collapsed into one another where a rollup separates
                  them, and that the report say which one was used.
    l1_commit_tx  the Ethereum transaction that posted the batch (C2)
    l1_prove_tx   the Ethereum transaction that proved it (C2)
    l1_execute_tx the Ethereum transaction that executed it, later still
    l1_commit_block  L1 block number for the commit - D2 costs from its receipt
    l1_prove_block   L1 block number for the proof
    outcome       success | reverted | timeout | rejected | submitted (B5)
    detail        why, when the outcome is not success
    gas_limit     what we asked for
    gas_used      what the receipt reported - the only figure D2 may cost from
    gas_price_wei effective price at submission
    block         L2 block number
"""

import json
from pathlib import Path
from typing import Any, Iterator

REPO_ROOT = Path(__file__).resolve().parents[2]
RESULTS_DIR = REPO_ROOT / "bench" / "results"

# Every timestamp field, in finality order. Kept in one place because the
# resolve pass decides what work is outstanding by asking which of these is
# still None.
TIMESTAMPS = ("t0", "t1", "t2", "t3")


class Outcome:
    """What happened to one transaction.

    Explicit rather than inferred from which fields are populated: a missing t1
    could mean the transaction timed out, or that the resolve pass has not run
    yet, and those two must never be confused in a latency statistic.
    """

    SUBMITTED = "submitted"   # broadcast, receipt not yet seen
    SUCCESS = "success"       # receipt with status 1
    REVERTED = "reverted"     # receipt with status 0 - on-chain, but failed
    TIMEOUT = "timeout"       # no receipt within the deadline
    REJECTED = "rejected"     # the node refused it; there is no hash

    #: Outcomes that may contribute to latency statistics. Everything else is
    #: reported as a failure rate beside them, never silently dropped (B5).
    MEASURABLE = (SUCCESS,)


def new_record(**fields: Any) -> dict:
    """A record with every schema field present, so rows stay rectangular."""
    record: dict[str, Any] = {
        "run_id": None,
        "network": None,
        "chain_id": None,
        "workload": None,
        "hash": None,
        "nonce": None,
        "t0": None,
        "t1": None,
        "l2_block_ts": None,
        "t2": None,
        "t3": None,
        "t3_kind": None,
        "t3_source": None,
        "batch": None,
        "batch_tx_count": None,
        "settle_status": None,
        "l1_commit_tx": None,
        "l1_prove_tx": None,
        "l1_execute_tx": None,
        "l1_commit_block": None,
        "l1_prove_block": None,
        "outcome": None,
        "detail": None,
        "gas_limit": None,
        "gas_used": None,
        "gas_price_wei": None,
        "block": None,
    }
    if fields:
        unknown = set(fields) - set(record)
        if unknown:
            raise KeyError(
                f"not in the record schema: {', '.join(sorted(unknown))}"
            )
        record.update(fields)
    return record


def run_path(run_id: str) -> Path:
    """Where one run's rows live. results/ is gitignored scratch (D1)."""
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    return RESULTS_DIR / f"{run_id}.jsonl"


def append(path: Path, records: list[dict]) -> None:
    """Append rows. Flushed per call so a kill loses at most the current batch."""
    with path.open("a") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")


def normalise(row: dict) -> dict:
    """One row with every current schema field present.

    Rows written before a field existed would otherwise be missing it, and a
    file whose rows have different keys is not a table - pandas would fill the
    gaps with NaN and D1's export would have ragged columns. Unknown keys are
    kept rather than dropped, so reading a file written by a newer version
    never destroys data.
    """
    merged = new_record()
    merged.update(row)
    return merged


def load(path: Path) -> list[dict]:
    """Every row in a file, tolerating a truncated final line.

    A partly-written last line is the expected cost of appending to JSON Lines
    and is worth skipping rather than crashing on - the other rows are intact.
    """
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(normalise(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return rows


def rewrite(path: Path, records: list[dict]) -> None:
    """Replace a file's contents. Written to a sibling then moved, so an
    interrupted resolve pass cannot leave a half-written run file behind."""
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    temp.replace(path)


def iter_runs() -> Iterator[Path]:
    """Every run file on disk, oldest first."""
    if not RESULTS_DIR.exists():
        return iter(())
    return iter(sorted(RESULTS_DIR.glob("*.jsonl")))
