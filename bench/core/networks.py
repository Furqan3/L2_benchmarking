"""Network definitions and connections (tasks A3, A4).

Every endpoint, chain id and explorer URL lives in bench/configs/networks.yaml,
never inline in code, so that a run is described entirely by committed config.

An RPC override may be supplied through the environment, which is how a public
endpoint gets swapped for an Alchemy or Infura URL once rate limits start to
bite in phase C:

    BENCH_RPC_SEPOLIA=https://eth-sepolia.g.alchemy.com/v2/...
"""

import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from web3 import Web3

from bench.core.wallet import load_env

REPO_ROOT = Path(__file__).resolve().parents[2]
NETWORKS_CONFIG = REPO_ROOT / "bench" / "configs" / "networks.yaml"

RPC_OVERRIDE_PREFIX = "BENCH_RPC_"


@dataclass(frozen=True)
class Network:
    """One chain we submit to or read from."""

    key: str
    display_name: str
    role: str                      # "l1" or "l2"
    chain_id: int                  # what we expect; verified at connect time
    rpc: str
    explorer: str
    family: str | None = None      # "zk" or "optimistic", for L2s
    settles_on: str | None = None  # key of the L1 this rollup posts to
    bridge: str | None = None
    notes: str | None = None
    # Where this rollup posts its batches on the L1, and the ABI committed for
    # it (task C1). Two of the three finality timestamps are read from here.
    l1_contract: str | None = None
    l1_abi: str | None = None
    #: Where batches are submitted *through*. Not the state-holding contract:
    #: getters on it revert. C2's fallback route needs both to tell a commit
    #: transaction's recipient from the contract whose logs carry the events.
    l1_validator_timelock: str | None = None
    #: Optimistic-rollup settlement addresses (task E1). Batches go to an inbox
    #: from a known batcher; output roots are proposed to a dispute game
    #: factory and become trustless only after a challenge period.
    l1_batch_inbox: str | None = None
    l1_batcher: str | None = None
    l1_dispute_game_factory: str | None = None
    l1_portal: str | None = None

    @property
    def is_l2(self) -> bool:
        return self.role == "l2"

    def address_url(self, address: str) -> str:
        return f"{self.explorer.rstrip('/')}/address/{address}"

    def tx_url(self, tx_hash: str) -> str:
        """An explorer link for a transaction hash, prefixed or not.

        Note the explicit prefix test rather than lstrip("0x"): lstrip strips
        any leading '0' and 'x' characters, so a hash beginning with a zero
        would lose it and the link would point at nothing.
        """
        if not tx_hash.startswith(("0x", "0X")):
            tx_hash = "0x" + tx_hash
        return f"{self.explorer.rstrip('/')}/tx/{tx_hash}"


def _config() -> dict:
    return yaml.safe_load(NETWORKS_CONFIG.read_text()) or {}


def load_networks() -> dict[str, Network]:
    """Every network in the config, keyed by its short name."""
    load_env()
    out: dict[str, Network] = {}
    for key, spec in (_config().get("networks") or {}).items():
        var = f"{RPC_OVERRIDE_PREFIX}{key.upper()}"
        rpc = os.environ.get(var, spec["rpc"])
        if "://" not in rpc:
            # The easy mistake is pasting the provider's project id or API key
            # rather than the whole URL. Caught here with the fix spelled out,
            # because the alternative is a MissingSchema traceback from deep
            # inside requests that says nothing about which variable is wrong.
            raise ValueError(
                f"{var} is not a URL: '{rpc[:12]}...'. It needs the full "
                f"endpoint, for example "
                f"https://sepolia.infura.io/v3/<project-id>"
            )
        out[key] = Network(
            key=key,
            display_name=spec["display_name"],
            role=spec["role"],
            chain_id=int(spec["chain_id"]),
            rpc=rpc,
            explorer=spec["explorer"],
            family=spec.get("family"),
            settles_on=spec.get("settles_on"),
            bridge=spec.get("bridge"),
            notes=spec.get("notes"),
            l1_contract=spec.get("l1_contract"),
            l1_abi=spec.get("l1_abi"),
            l1_validator_timelock=spec.get("l1_validator_timelock"),
            l1_batch_inbox=spec.get("l1_batch_inbox"),
            l1_batcher=spec.get("l1_batcher"),
            l1_dispute_game_factory=spec.get("l1_dispute_game_factory"),
            l1_portal=spec.get("l1_portal"),
        )
    return out


def funding_priority() -> list[str]:
    """Networks this study actually needs funded, most important first."""
    return list(_config().get("funding_priority") or [])


def faucets() -> dict[str, list[dict]]:
    """Faucet options, grouped by how hard they are to obtain from."""
    return _config().get("faucets") or {}


# Ten seconds is ample for a balance or a receipt and far too tight for the
# log queries phase C issues over thousands of blocks, which routinely take
# tens of seconds even when the endpoint is healthy. A read timeout there is
# indistinguishable from an endpoint that has no such logs, so it defaults
# generously and callers doing quick polls can pass something shorter.
DEFAULT_TIMEOUT_S = 60.0


def connect(network: Network, timeout: float = DEFAULT_TIMEOUT_S) -> Web3:
    """An HTTP connection to one network. Does not verify the chain id."""
    return Web3(Web3.HTTPProvider(network.rpc, request_kwargs={"timeout": timeout}))


class ChainIdMismatch(RuntimeError):
    """The endpoint is not the chain the config claims it is.

    Worth failing loudly on: an endpoint quietly pointing at the wrong chain
    produces results that look plausible and describe the wrong system.
    """


def connect_verified(network: Network, timeout: float = DEFAULT_TIMEOUT_S) -> Web3:
    """Connect and confirm the endpoint reports the chain id we expect."""
    w3 = connect(network, timeout=timeout)
    actual = w3.eth.chain_id
    if actual != network.chain_id:
        raise ChainIdMismatch(
            f"{network.key}: config says chain {network.chain_id}, "
            f"endpoint {network.rpc} reports {actual}"
        )
    return w3
