"""OP Stack settlement lookup (tasks E1, E2).

An optimistic rollup gives you none of what a ZK rollup hands over. There is no
method that takes an L2 hash and returns the L1 transactions that settled it, so
both timestamps have to be reconstructed - and neither reconstruction is as
strong as zkSync's.

    t2 - MATCHED, NOT LOOKED UP

Batches go from a known batcher address to a fixed inbox address as blob
transactions. Proving that a particular batch blob contains a particular L2
block would mean decoding and decompressing the blob, which is a channel-frame
decoder this project does not need.

Instead: read the L1 origin of our L2 block from the L1Block predeploy, then
scan forward on the L1 for the first batcher transaction after it. Batches are
posted in order and cover contiguous L2 block ranges, so the first posting after
our block is the one that carries it, or very nearly. That is a real L1
transaction and a defensible timestamp, but it is matched by proximity rather
than proven, so it is recorded as t2_kind = estimated. A declared estimate is a
limitation; an undeclared one is a fabrication.

    t3 - DERIVED, AND NOT OBSERVABLE HERE AT ALL

For a ZK rollup, trustless finality is a proof being verified: an event, at a
time, in a block. For an optimistic rollup it is the absence of a successful
challenge before a deadline. Nothing happens at that moment. There is no
transaction to point at.

So t3 is computed - the time the output root covering our block was proposed,
plus the challenge period - and marked derived. It is a deadline, not an event.

Measured on OP Sepolia 2026-08-10, reading the OptimismPortal directly:

    proofMaturityDelaySeconds        604800   seven days
    disputeGameFinalityDelaySeconds  302400   three and a half days
    maxClockDuration (game)          302400   three and a half days

which is the same as OP Mainnet. The common assumption that testnets shorten
their challenge windows to something like an hour is simply not true here, and
it means t3 for this rollup cannot be observed inside an eight-week project
under any circumstances. That is a finding, not an obstacle.
"""

from bench.adapters.base import (
    DERIVED,
    ESTIMATED,
    OBSERVED,
    Settlement,
    SettlementUnavailable,
)

#: Every OP Stack L2 stores its current L1 origin at this predeploy.
L1_BLOCK_PREDEPLOY = "0x4200000000000000000000000000000000000015"

#: How far to scan the L1 forward once anchored at the right point in time.
#: OP Sepolia posted twice in a 25-block sample, so roughly one batch every
#: dozen Sepolia blocks; sixty is several batches of headroom.
#:
#: This used to be 300, scanning forward from the L2 block's L1 origin. That
#: origin lags the head by a sequencer window, so most of those 300 blocks were
#: before our transaction even existed - fetched with full transaction bodies,
#: and discarded. Anchoring by timestamp first made the scan five times smaller
#: and started it in the right place.
BATCH_SCAN_BLOCKS = 60

#: How far back through the dispute game factory to look. OP Sepolia proposes
#: roughly four output roots an hour, so this covers several days - ample for a
#: transaction submitted during a run, and bounded so a transaction that will
#: never be proposed cannot walk 80,000 games.
GAME_SCAN_LIMIT = 400

_MIN_ABI = [
    {"type": "function", "name": "number", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint64"}]},
]

_PORTAL_ABI = [
    {"type": "function", "name": n, "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": t}]}
    for n, t in (("proofMaturityDelaySeconds", "uint256"),
                 ("disputeGameFinalityDelaySeconds", "uint256"),
                 ("respectedGameType", "uint32"))
]

_DGF_ABI = [
    {"type": "function", "name": "gameCount", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint256"}]},
    {"type": "function", "name": "gameAtIndex", "stateMutability": "view",
     "inputs": [{"name": "i", "type": "uint256"}],
     "outputs": [{"name": "gameType", "type": "uint32"},
                 {"name": "timestamp", "type": "uint64"},
                 {"name": "proxy", "type": "address"}]},
]

_GAME_ABI = [
    {"type": "function", "name": n, "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": t}]}
    for n, t in (("l2BlockNumber", "uint256"), ("createdAt", "uint64"),
                 ("resolvedAt", "uint64"), ("status", "uint8"),
                 ("maxClockDuration", "uint64"))
]

# These are minimal read-only interface definitions rather than full committed
# ABIs. The risk profile is different from calldata: a wrong entry here fails
# loudly on decode or reverts, it cannot silently corrupt a transaction. Every
# value read through them was sanity-checked against the superchain registry.

#: Batcher postings found so far and the L1 blocks already inspected, shared
#: across every row in a resolve pass so no block is fetched twice.
_postings: list[dict] = []
_scanned_blocks: set[int] = set()
_game_cache: dict[int, list] = {}
_l2_block_cache: dict[int, int] = {}


class OptimismAdapter:
    """Settlement for OP Stack chains, reconstructed rather than looked up."""

    family = "optimistic"

    def __init__(self, network=None):
        self.network = network

    # -- t2 ---------------------------------------------------------------

    def l1_origin(self, w3_l2, l2_block: int) -> int:
        """The L1 block our L2 block derived from, read at that L2 block."""
        contract = w3_l2.eth.contract(
            address=w3_l2.to_checksum_address(L1_BLOCK_PREDEPLOY),
            abi=_MIN_ABI,
        )
        return contract.functions.number().call(block_identifier=l2_block)

    def block_at_or_after(self, w3_l1, timestamp: int, low: int) -> int:
        """First L1 block at or after a wall-clock time, by binary search.

        Uses light block headers, not full transaction bodies: about twenty
        small requests instead of hundreds of large ones.
        """
        high = w3_l1.eth.block_number
        if low >= high:
            return high
        while low < high:
            mid = (low + high) // 2
            try:
                stamp = w3_l1.eth.get_block(mid)["timestamp"]
            except Exception:  # noqa: BLE001
                return low
            if stamp < timestamp:
                low = mid + 1
            else:
                high = mid
        return low

    def scan_postings(self, w3_l1, net, first: int, last: int) -> None:
        """Index every batcher posting in an L1 block range, once.

        Scanning per transaction was the obvious implementation and it was far
        too slow to use: fifty rows each walking three hundred blocks with full
        transaction bodies is fifteen thousand requests for an answer that lives
        in one pass over the same range. Postings are collected into a shared
        index instead, and each block is fetched at most once per process.
        """
        batcher = (net.l1_batcher or "").lower()
        inbox = (net.l1_batch_inbox or "").lower()
        if not batcher or not inbox:
            raise SettlementUnavailable(
                f"{net.key}: l1_batcher and l1_batch_inbox must be configured"
            )

        for number in range(first, last + 1):
            if number in _scanned_blocks:
                continue
            try:
                block = w3_l1.eth.get_block(number, full_transactions=True)
            except Exception:  # noqa: BLE001
                continue
            _scanned_blocks.add(number)
            for tx in block["transactions"]:
                if ((tx["from"] or "").lower() == batcher
                        and (tx["to"] or "").lower() == inbox):
                    _postings.append({
                        "hash": tx["hash"].hex() if hasattr(tx["hash"], "hex")
                        else str(tx["hash"]),
                        "block": number,
                        "timestamp": block["timestamp"],
                    })
        _postings.sort(key=lambda p: p["timestamp"])

    def find_batch_posting(self, w3_l1, net, from_block: int,
                           not_before: int) -> dict | None:
        """First batcher posting at or after from_block, and not_before in time.

        The time guard is not redundant with the block guard, and leaving it out
        was a real bug. The L1 origin of an L2 block lags the L1 head by a
        sequencer window, so scanning forward from it finds batch postings that
        went out BEFORE our transaction was ever submitted. The first such match
        produced a t2 nine seconds earlier than t0 - a batch that could not
        possibly contain our transaction, and a negative latency.

        Bounded. An unbounded scan is the fastest way to be rate limited, and a
        batch not posted within the window has genuinely not been posted yet -
        which the resolve pass will retry.
        """
        head = w3_l1.eth.block_number
        # Anchor at the first L1 block that could possibly carry our batch,
        # rather than at the L2 block's L1 origin which is a window behind it.
        anchor = max(from_block,
                     self.block_at_or_after(w3_l1, not_before, from_block))
        self.scan_postings(w3_l1, net, anchor,
                           min(anchor + BATCH_SCAN_BLOCKS, head))
        for posting in _postings:
            if posting["block"] >= from_block and posting["timestamp"] >= not_before:
                return posting
        return None

    # -- t3 ---------------------------------------------------------------

    def challenge_period(self, w3_l1, net) -> int:
        """Seconds from output proposal to trustless finality.

        proofMaturityDelaySeconds is the binding one: it is how long the portal
        makes a withdrawal wait after its proof, and therefore how long before
        the state can be relied on without trusting the proposer.
        """
        portal = w3_l1.eth.contract(
            address=w3_l1.to_checksum_address(net.l1_portal), abi=_PORTAL_ABI
        )
        return int(portal.functions.proofMaturityDelaySeconds().call())

    def respected_game_type(self, w3_l1, net) -> int:
        """The only game type that decides finality, per the portal."""
        portal = w3_l1.eth.contract(
            address=w3_l1.to_checksum_address(net.l1_portal), abi=_PORTAL_ABI
        )
        return int(portal.functions.respectedGameType().call())

    def game_covering(self, w3_l1, net, l2_block: int) -> dict | None:
        """The first respected dispute game whose output root covers our block.

            WHY THIS IS A BACKWARD SCAN AND NOT A BINARY SEARCH

        It was a binary search, and the binary search was wrong in two ways that
        produced confidently incorrect data rather than an error.

        First, gameAtIndex is ordered by creation, not by L2 block, and the
        sequence is not monotonic. Game 62192 on OP Sepolia reports an
        l2BlockNumber of 1,767,657,607 - which is not a block number at all, it
        is a Unix timestamp - on a chain whose head is around 47 million. A
        binary search over a sequence containing that lands wherever the
        garbage sends it.

        Second, it ignored gameType. That game is type 0; the portal's
        respectedGameType is 8. A game of an unrespected type has no bearing on
        finality and must not be consulted at all.

        Together they matched 196 transactions submitted in August to a game
        created the previous January, giving each of them a trustless timestamp
        209 days BEFORE it was submitted.

        Scanning backward from the newest game is cheap - OP Sepolia proposes
        roughly four an hour, so a few hundred games covers days - and it does
        not assume an ordering the data does not have.
        """
        factory = w3_l1.eth.contract(
            address=w3_l1.to_checksum_address(net.l1_dispute_game_factory),
            abi=_DGF_ABI,
        )
        count = factory.functions.gameCount().call()
        if count == 0:
            return None

        respected = self.respected_game_type(w3_l1, net)
        best: dict | None = None

        for index in range(count - 1, max(-1, count - 1 - GAME_SCAN_LIMIT), -1):
            if index not in _game_cache:
                try:
                    _game_cache[index] = factory.functions.gameAtIndex(index).call()
                except Exception:  # noqa: BLE001
                    continue
            game_type, created_at, proxy = _game_cache[index]

            if int(game_type) != respected:
                continue

            if index not in _l2_block_cache:
                try:
                    game = w3_l1.eth.contract(address=proxy, abi=_GAME_ABI)
                    _l2_block_cache[index] = int(
                        game.functions.l2BlockNumber().call()
                    )
                except Exception:  # noqa: BLE001
                    continue
            covered = _l2_block_cache[index]

            if covered >= l2_block:
                # A candidate. Keep walking back for an earlier one that still
                # covers our block, since we want the first proposal to do so.
                best = {"index": index, "proxy": proxy,
                        "game_type": int(game_type),
                        "created_at": int(created_at), "l2_block": covered}
            else:
                # Respected games do run in ascending L2 block order, so the
                # first one that falls short means we have gone far enough.
                break

        return best

    # -- interface --------------------------------------------------------

    def settlement(self, w3_l2, w3_l1, net, tx_hash: str) -> Settlement:
        try:
            receipt = w3_l2.eth.get_transaction_receipt(tx_hash)
        except Exception as exc:  # noqa: BLE001
            raise SettlementUnavailable(
                f"no L2 receipt for {tx_hash}: {type(exc).__name__}"
            ) from exc

        l2_block = receipt["blockNumber"]
        origin = self.l1_origin(w3_l2, l2_block)
        l2_timestamp = w3_l2.eth.get_block(l2_block)["timestamp"]
        posting = self.find_batch_posting(w3_l1, net, origin, l2_timestamp)
        game = self.game_covering(w3_l1, net, l2_block)

        commit_tx = posting["hash"] if posting else None
        if commit_tx and not commit_tx.startswith("0x"):
            commit_tx = "0x" + commit_tx

        # Computed here, not looked up: nothing happens when a challenge window
        # closes, so there is no transaction whose block timestamp is t3.
        derived = None
        if game:
            derived = game["created_at"] + self.challenge_period(w3_l1, net)

        return Settlement(
            commit_tx=commit_tx,
            prove_tx=None,
            t3_derived_at=derived,
            batch=game["index"] if game else None,
            # An OP batch's transaction count is not exposed without decoding
            # the blob. Left None rather than guessed, so the cost model
            # reports it unavailable instead of dividing by a made-up number.
            batch_tx_count=None,
            status="proposed" if game else "unproposed",
            t2_kind=ESTIMATED if posting else OBSERVED,
            t3_kind=DERIVED,
            t3_source="challenge_window" if game else None,
        )

