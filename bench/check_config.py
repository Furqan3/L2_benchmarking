"""Find config keys that nothing reads (task D5).

    python -m bench.check_config

A configuration option that is parsed and then ignored is the worst class of
bug in a benchmark: you change a setting, the run completes, the number is
unchanged, and nothing tells you. This is the done-when test for D5 - every key
in every config file either changes something observable, or is reported here.

    WHAT IT CHECKS AND WHAT IT CANNOT

It looks for each schema key as a string literal in the source. That catches a
key nothing ever names, which is the failure mode that matters.

It deliberately skips the *names* inside name-to-spec mappings - the network
keys under 'networks:', the workload names under 'workloads:', the entries
under 'tokens:'. Those are data iterated over, never named in code, so treating
them as dead keys would drown the real findings in false positives.

It cannot tell that a key is read but then ignored. That still needs eyes.

Exit status is 0 when nothing is unread, 1 otherwise.
"""

import argparse
import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "bench" / "configs"
SOURCE_DIR = REPO_ROOT / "bench"

#: Mappings whose keys are data, not schema. Their values' keys are still
#: checked - only the level of names directly under them is skipped.
DATA_MAPPINGS = {"networks", "workloads", "tokens", "faucets"}

#: Keys that exist to be read by a person, not by code. Each needs a reason.
DOCUMENTED_EXEMPT = {
    "notes": "prose for the reader; deliberately never parsed",
}


def schema_keys(node, parent: str = "", skip_names: bool = False):
    """Every key in a config that represents schema rather than data."""
    if isinstance(node, dict):
        for key, value in node.items():
            if not skip_names:
                yield str(key), parent
            yield from schema_keys(
                value, parent=str(key), skip_names=str(key) in DATA_MAPPINGS
            )
    elif isinstance(node, list):
        for item in node:
            yield from schema_keys(item, parent=parent, skip_names=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quiet", action="store_true",
                    help="print only the unread keys")
    args = ap.parse_args()

    source = "\n".join(
        p.read_text() for p in SOURCE_DIR.rglob("*.py")
        if p.name != Path(__file__).name
    )

    unread: list[tuple[str, str]] = []
    for config in sorted(CONFIG_DIR.glob("*.yaml")):
        parsed = yaml.safe_load(config.read_text()) or {}
        seen: set[str] = set()
        for key, _parent in schema_keys(parsed):
            if key in seen:
                continue
            seen.add(key)
            if key in DOCUMENTED_EXEMPT:
                if not args.quiet:
                    print(f"  {config.name:<16} {key:<24} exempt "
                          f"({DOCUMENTED_EXEMPT[key]})")
                continue
            if re.search(rf'["\']{re.escape(key)}["\']', source):
                if not args.quiet:
                    print(f"  {config.name:<16} {key:<24} read")
            else:
                unread.append((config.name, key))
                print(f"  {config.name:<16} {key:<24} UNREAD")

    if unread:
        print(f"\n{len(unread)} key(s) nothing reads:")
        for name, key in unread:
            print(f"  {name}: {key}")
        print("\nDelete them, or make them change something observable. A "
              "setting that is parsed and ignored changes a number silently.")
        return 1

    print("\nEvery config key is read by something. D5 done-when satisfied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
