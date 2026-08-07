"""Test-wallet loading (task A2).

The private key lives only in .env at the repo root, which is gitignored.
The address lives in bench/configs/accounts.yaml, which is committed so that
every result can be traced back to the account that produced it.
"""

import os
from pathlib import Path

import yaml
from eth_account import Account
from eth_account.signers.local import LocalAccount

REPO_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = REPO_ROOT / ".env"
ACCOUNTS_CONFIG = REPO_ROOT / "bench" / "configs" / "accounts.yaml"

KEY_VAR = "BENCH_PRIVATE_KEY"


def load_env(path: Path = ENV_PATH) -> None:
    """Read KEY=VALUE lines from .env into os.environ.

    A variable already present in the real environment wins, so a run can be
    pointed at a different account without editing the file.
    """
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def load_account() -> LocalAccount:
    """Return the test account, loaded from the environment."""
    load_env()
    key = os.environ.get(KEY_VAR)
    if not key:
        raise RuntimeError(
            f"{KEY_VAR} is not set. Run: python -m bench.create_wallet"
        )
    return Account.from_key(key)


def configured_address(path: Path = ACCOUNTS_CONFIG) -> str | None:
    """The address recorded in the committed config, if there is one."""
    if not path.exists():
        return None
    cfg = yaml.safe_load(path.read_text()) or {}
    return (cfg.get("account") or {}).get("address")


def check_address() -> str:
    """Load the key and confirm it derives the address we committed.

    A mismatch means .env and the config describe different accounts, which
    would make every recorded result untraceable.
    """
    acct = load_account()
    expected = configured_address()
    if expected and expected.lower() != acct.address.lower():
        raise RuntimeError(
            f"{KEY_VAR} derives {acct.address}, but {ACCOUNTS_CONFIG.name} "
            f"records {expected}."
        )
    return acct.address


if __name__ == "__main__":
    print(check_address())
