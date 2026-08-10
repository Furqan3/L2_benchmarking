"""Workload definitions and calldata construction (task B1).

Two workloads - a native transfer and an ERC-20 transfer - each of which turns
into a transaction dict that B2 can sign and broadcast unchanged.

The rule this module exists to enforce: no function selector and no calldata is
ever written by hand. Selectors are derived from the committed ABI, arguments go
through the ABI encoder, and a signature typed into a string literal anywhere in
this file would defeat the point. Hand-encoded calldata is the failure mode the
handbook warns about because a corrupt selector passes every local check and
fails only on first real submission.

Gas is deliberately NOT set here. Building and estimating are separate steps so
that an estimation failure is reported as an estimation failure - see B2, which
estimates per transaction rather than hardcoding a limit.
"""

import json
from dataclasses import dataclass
from pathlib import Path

import yaml
from eth_typing import ChecksumAddress
from eth_utils.abi import function_abi_to_4byte_selector
from web3 import Web3

REPO_ROOT = Path(__file__).resolve().parents[2]
ABI_DIR = REPO_ROOT / "bench" / "abis"
WORKLOADS_CONFIG = REPO_ROOT / "bench" / "configs" / "workloads.yaml"

NATIVE = "native"
ERC20 = "erc20"


@dataclass(frozen=True)
class Workload:
    """One kind of transaction we submit, independent of any network."""

    name: str
    kind: str                 # NATIVE or ERC20
    description: str
    value_wei: int = 0        # native only
    amount: int = 1           # erc20 only, in token units
    abi: str | None = None    # erc20 only, filename within abis/

    @property
    def needs_token(self) -> bool:
        return self.kind == ERC20


class TokenNotDeployed(RuntimeError):
    """No ERC-20 recorded for this network in workloads.yaml.

    Raised rather than silently falling back to a native transfer: a workload
    that quietly becomes a different workload would put two incomparable things
    in one column of the results.
    """


def _config() -> dict:
    return yaml.safe_load(WORKLOADS_CONFIG.read_text()) or {}


def load_workloads() -> dict[str, Workload]:
    """Every workload in the config, keyed by name."""
    out: dict[str, Workload] = {}
    for name, spec in (_config().get("workloads") or {}).items():
        out[name] = Workload(
            name=name,
            kind=spec["kind"],
            description=spec.get("description", ""),
            value_wei=int(spec.get("value_wei", 0)),
            amount=int(spec.get("amount", 1)),
            abi=spec.get("abi"),
        )
    return out


def recipient() -> ChecksumAddress:
    """The fixed address both workloads send to."""
    return Web3.to_checksum_address(_config()["recipient"])


def token_address(network_key: str) -> ChecksumAddress | None:
    """The ERC-20 recorded for one network, or None if none is deployed."""
    raw = (_config().get("tokens") or {}).get(network_key)
    return Web3.to_checksum_address(raw) if raw else None


def load_abi(filename: str) -> list:
    """An ABI committed under abis/.

    Committed rather than fetched at runtime so results do not depend on
    Etherscan being reachable on the day someone reproduces them.
    """
    return json.loads((ABI_DIR / filename).read_text())


def selector(abi: list, function_name: str) -> str:
    """The four-byte selector for one ABI function, derived not typed.

    Exists so the check script can display what it is about to send. The
    signature string is reconstructed from the ABI's own input types; nothing
    here trusts a signature written by a human.
    """
    for entry in abi:
        if entry.get("type") == "function" and entry.get("name") == function_name:
            return "0x" + function_abi_to_4byte_selector(entry).hex()
    raise KeyError(f"{function_name} is not in this ABI")


def signature(abi: list, function_name: str) -> str:
    """The canonical signature, rebuilt from the ABI. For display only."""
    for entry in abi:
        if entry.get("type") == "function" and entry.get("name") == function_name:
            types = ",".join(i["type"] for i in entry["inputs"])
            return f"{function_name}({types})"
    raise KeyError(f"{function_name} is not in this ABI")


def build(
    w3: Web3,
    account,
    workload: Workload,
    network_key: str,
    nonce: int | None = None,
) -> dict:
    """A transaction dict for one workload, without a gas limit.

    nonce is a parameter rather than always fetched, because B3 assigns
    consecutive nonces from a single count taken before a batch begins. Fetching
    inside each build would give concurrent transactions the same number.
    """
    if nonce is None:
        nonce = w3.eth.get_transaction_count(account.address)

    tx = {
        "from": account.address,
        "nonce": nonce,
        "chainId": w3.eth.chain_id,
        "gasPrice": w3.eth.gas_price,
    }

    if workload.kind == NATIVE:
        tx["to"] = recipient()
        tx["value"] = workload.value_wei
        return tx

    if workload.kind == ERC20:
        token = token_address(network_key)
        if token is None:
            raise TokenNotDeployed(
                f"no ERC-20 recorded for '{network_key}' in {WORKLOADS_CONFIG.name}"
            )
        if not workload.abi:
            raise ValueError(f"workload '{workload.name}' has no abi configured")
        contract = w3.eth.contract(address=token, abi=load_abi(workload.abi))
        tx["to"] = token
        tx["value"] = 0
        # encode_abi is the ABI encoder doing the work. Never string building.
        tx["data"] = contract.encode_abi(
            "transfer", args=[recipient(), workload.amount]
        )
        return tx

    raise ValueError(f"unknown workload kind '{workload.kind}'")
