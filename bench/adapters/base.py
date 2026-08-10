"""The interface every rollup adapter implements (tasks C2, E1).

One shape of answer for every architecture, so that the resolve pass never
branches on which rollup it is talking to. The architectures genuinely differ in
what trustless finality *means* - a ZK rollup verifies a proof, an optimistic
rollup waits out a challenge window - and that difference is carried in the
data as t3_kind rather than hidden by pretending the two are the same event.
"""

from dataclasses import dataclass
from typing import Protocol

#: t3 was read from an L1 transaction that actually happened.
OBSERVED = "observed"
#: t3 was computed as a deadline - proposal time plus a challenge period - and
#: no event was witnessed at that moment. E2 requires these be distinguishable
#: in the output, because presenting a computed deadline as an observation
#: would be the most misleading thing in the whole study.
DERIVED = "derived"


class SettlementUnavailable(RuntimeError):
    """Settlement for this transaction cannot be determined.

    Distinct from "has not settled yet", which is an ordinary Settlement with
    null fields. This means we cannot find out - a missing adapter, an RPC
    method the node does not implement - and it must never be recorded as
    though the batch simply had not been posted.
    """


@dataclass(frozen=True)
class Settlement:
    """Where one L2 transaction ended up on the L1.

    Every field may be None: batches are committed, then proven, then executed,
    and a transaction observed between those stages legitimately has only some
    of them. None means "not yet", and the resolve pass will ask again.
    """

    commit_tx: str | None = None    # posted the batch to L1 -> t2
    prove_tx: str | None = None     # proved it -> t3 for a ZK rollup
    execute_tx: str | None = None   # applied it to L1 state, later still
    batch: int | None = None        # the rollup's own batch number
    status: str | None = None       # the rollup's word for its stage
    t3_kind: str = OBSERVED
    t3_source: str | None = None    # which event t3 was taken from

    @property
    def complete(self) -> bool:
        """True when nothing further will change for this transaction."""
        return bool(self.commit_tx and self.prove_tx)


class Adapter(Protocol):
    """What a rollup adapter must provide."""

    family: str

    def settlement(self, w3_l2, tx_hash: str) -> Settlement:
        """Where this L2 transaction settled on the L1, as far as is known."""
        ...
