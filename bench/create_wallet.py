"""Generate the test wallet (task A2).

Writes the private key to .env (gitignored, mode 0600) and the address to
bench/configs/accounts.yaml (committed). Refuses to overwrite an existing key:
losing it means losing access to whatever funds the faucets gave you.

    python -m bench.create_wallet
"""

import datetime as dt
import os
import sys

from eth_account import Account

from bench.core.wallet import ACCOUNTS_CONFIG, ENV_PATH, KEY_VAR, load_env


def main() -> int:
    load_env()
    if os.environ.get(KEY_VAR):
        print(
            f"{KEY_VAR} is already set (see {ENV_PATH}). Delete it by hand "
            "first if you really want a new account.",
            file=sys.stderr,
        )
        return 1

    acct = Account.create()

    with open(ENV_PATH, "a") as f:
        f.write(f"\n# Test wallet, generated {dt.date.today()}. Testnet only.\n")
        f.write(f"{KEY_VAR}={acct.key.hex()}\n")
    os.chmod(ENV_PATH, 0o600)

    ACCOUNTS_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    ACCOUNTS_CONFIG.write_text(
        "# Test account for L2 finality benchmarking (task A2).\n"
        "# Testnet only - this account must never hold real funds.\n"
        f"# The private key is in .env as {KEY_VAR}, which is gitignored.\n"
        "account:\n"
        f'  address: "{acct.address}"\n'
        f"  created: {dt.date.today()}\n"
        "  networks: []  # filled in as faucets pay out (task A3)\n"
    )

    print(f"address: {acct.address}")
    print(f"key written to: {ENV_PATH}")
    print(f"address recorded in: {ACCOUNTS_CONFIG}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
