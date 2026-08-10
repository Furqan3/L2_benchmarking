"""Per-rollup settlement lookup - the only network-specific code (tasks C2, E1).

Everything else in the framework works in terms of the Settlement record these
adapters return, so adding a rollup means adding one file here and nothing else.
That is what lets E1's done-when - the same workload running unmodified against
both a ZK and an optimistic rollup - be true rather than aspirational.
"""

from bench.adapters.base import Adapter, Settlement, SettlementUnavailable
from bench.adapters.optimism import OptimismAdapter
from bench.adapters.zksync import ZkSyncAdapter

#: Keyed by the 'family' field in networks.yaml.
_BY_FAMILY: dict[str, Adapter] = {
    "zk": ZkSyncAdapter(),
    "optimistic": OptimismAdapter(),
}


def for_network(network) -> Adapter:
    """The adapter for one network.

    Raises rather than returning a do-nothing default: a rollup with no adapter
    would otherwise silently produce rows with no t2 and no t3, which look
    exactly like rows whose settlement has not happened yet.
    """
    adapter = _BY_FAMILY.get(network.family or "")
    if adapter is None:
        raise SettlementUnavailable(
            f"no settlement adapter for family '{network.family}' "
            f"(network {network.key})"
        )
    return adapter


__all__ = [
    "Adapter",
    "Settlement",
    "SettlementUnavailable",
    "OptimismAdapter",
    "ZkSyncAdapter",
    "for_network",
]
