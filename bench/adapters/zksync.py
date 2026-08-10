"""zkSync Era settlement lookup (task C2, preferred route).

zks_getTransactionDetails takes an L2 hash and hands back the L1 commit, prove
and execute transaction hashes directly. That is the shortcut the handbook says
to try before writing any log-parsing code, and it collapses C2, C3 and C4 into
a poll. Verified working against zkSync Era Sepolia on 2026-08-10.

The fields are null until each stage completes, so a transaction seen shortly
after submission has a commit hash and nothing else. That is not an error - it
is the resolve pass's entire reason for existing.

    WHICH HASH IS t3

zkSync distinguishes proving from executing. The proof verification is the
moment the L1 can no longer be persuaded the state is wrong, so ethProveTxHash
is t3 and t3_source records that. ethExecuteTxHash is kept as well, because C4
step 3 asks that the distinction be recorded rather than collapsed.
"""

from bench.adapters.base import OBSERVED, Settlement

METHOD = "zks_getTransactionDetails"


def _clean(value) -> str | None:
    """A hash, or None for the nulls this API returns before each stage.

    Also rejects the all-zero hash. Some rollup APIs use it as a placeholder
    for "not yet", and it would otherwise be stored as though it were a real
    L1 transaction and then fail to resolve in an explorer much later.
    """
    if not value or not isinstance(value, str):
        return None
    if set(value.lower().removeprefix("0x")) == {"0"}:
        return None
    return value


class ZkSyncAdapter:
    """Settlement via zkSync's own transaction-details RPC."""

    family = "zk"

    def settlement(self, w3_l2, tx_hash: str) -> Settlement:
        response = w3_l2.provider.make_request(METHOD, [tx_hash])

        error = response.get("error")
        if error:
            # Surfaced rather than swallowed. A node that does not implement
            # this method needs the fallback route, and that is a decision for
            # the caller, not something to paper over with empty fields.
            from bench.adapters.base import SettlementUnavailable

            raise SettlementUnavailable(
                f"{METHOD}: {error.get('message', error)}"
            )

        details = response.get("result") or {}
        prove = _clean(details.get("ethProveTxHash"))

        return Settlement(
            commit_tx=_clean(details.get("ethCommitTxHash")),
            prove_tx=prove,
            execute_tx=_clean(details.get("ethExecuteTxHash")),
            status=details.get("status"),
            t3_kind=OBSERVED,
            t3_source="prove" if prove else None,
        )
