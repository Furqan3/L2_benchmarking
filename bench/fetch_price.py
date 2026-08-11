"""Fetch the ETH/USD rate and record it with its provenance (task D2 step 2).

    python -m bench.fetch_price           # fetch and write pricing.yaml
    python -m bench.fetch_price --show    # print what is recorded now

Two independent sources are queried and compared. If they disagree by more than
a small margin the disagreement is reported rather than silently averaged - a
spot price that two exchanges cannot agree on is a fact worth knowing before it
is multiplied through every cost figure in the study.

    WHY THIS IS A SCRIPT AND NOT A TYPED NUMBER

The rate began life as a hand-entered 3800.0 marked PLACEHOLDER. It was wrong by
more than a factor of two, and every dollar figure derived from it was wrong by
the same factor. The ETH costs were never affected - those come from L1 receipts
- but the conversion did, and nothing in the output could have revealed it.

A fetched rate carries the one thing a typed one cannot: a timestamp and a named
source, so a reader can check what the number was at the moment it was used.
Costs are still converted at analysis time, never at collection time, so
re-running the export with a corrected rate is a second of work rather than a
repeat of the experiment.
"""

import argparse
import datetime as dt
import json
import urllib.request

import yaml

from bench.core.costs import PRICING_CONFIG

SOURCES = (
    ("CoinGecko",
     "https://api.coingecko.com/api/v3/simple/price"
     "?ids=ethereum&vs_currencies=usd",
     lambda d: float(d["ethereum"]["usd"])),
    ("Coinbase",
     "https://api.coinbase.com/v2/prices/ETH-USD/spot",
     lambda d: float(d["data"]["amount"])),
)

#: Two spot prices for the same asset should agree closely. Beyond this, say so.
TOLERANCE = 0.02


def fetch_one(url: str, extract) -> float:
    request = urllib.request.Request(
        url, headers={"User-Agent": "l2-benchmark/1.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return extract(json.load(response))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--show", action="store_true",
                    help="print the recorded rate and exit")
    args = ap.parse_args()

    config = yaml.safe_load(PRICING_CONFIG.read_text()) or {}

    if args.show:
        current = config.get("eth_usd") or {}
        print(f"\nrate         {current.get('rate')}")
        print(f"recorded at  {current.get('recorded_at')}")
        print(f"source       {str(current.get('source', '')).strip()}\n")
        return 0

    quotes: dict[str, float] = {}
    for name, url, extract in SOURCES:
        try:
            quotes[name] = fetch_one(url, extract)
            print(f"  {name:<12} {quotes[name]:,.2f} USD")
        except Exception as exc:  # noqa: BLE001
            print(f"  {name:<12} unavailable ({type(exc).__name__})")

    if not quotes:
        print("\nNo source responded. The recorded rate is unchanged.\n")
        return 1

    rate = sum(quotes.values()) / len(quotes)
    spread = ((max(quotes.values()) - min(quotes.values())) / rate
              if len(quotes) > 1 else 0.0)

    if spread > TOLERANCE:
        print(f"\nSources disagree by {spread * 100:.1f}%, beyond the "
              f"{TOLERANCE * 100:.0f}% tolerance. Recording anyway, but the "
              "disagreement belongs in the report.")

    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    config["eth_usd"] = {
        "rate": round(rate, 2),
        "recorded_at": stamp,
        "source": (
            f"Mean of {' and '.join(f'{k} {v:,.2f}' for k, v in quotes.items())}, "
            f"fetched {stamp} by bench/fetch_price.py. Spread "
            f"{spread * 100:.2f}%."
        ),
    }

    # Preserve the file's leading comments; safe_dump discards every one, and
    # those lines are what explain why the rate needs provenance at all.
    text = PRICING_CONFIG.read_text()
    header = []
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            header.append(line)
        else:
            break
    body = yaml.safe_dump(config, sort_keys=False, width=88)
    PRICING_CONFIG.write_text("\n".join(header + [body.rstrip()]) + "\n")

    print(f"\nrecorded {rate:,.2f} USD in {PRICING_CONFIG.name}")
    print("Re-run 'python -m bench.export' to convert costs at the new rate.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
