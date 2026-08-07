# L2_benchmarking

Measuring transaction finality on Ethereum Layer 2 rollups at three distinct
trust levels, and what it costs to reach each one.

| Level | Final when | Timestamp source |
|---|---|---|
| Full trust | the L2 sequencer accepts it | L2 RPC transaction receipt |
| Partial trust | its batch is posted to Ethereum | L1 batch-commit transaction |
| Trustless | its batch is proven, or the challenge window closes | a later L1 transaction |

Two of the three timestamps come from Ethereum rather than the rollup, so the
approach is purely observational: it measures public networks and hosts nothing.

Mitacs Globalink 2026, project 50081. Supervisor: Prof. Sara Rouhani, TCDT Lab.

## Status

Phase A complete — repository, wallet, network config, funding checks, and a
verified first transaction. Workload submission and finality measurement are
in progress.

## Setup

```sh
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m bench.create_wallet   # writes the key to .env, address to configs
```

`.env` holds a testnet-only private key and is gitignored. This account must
never hold real funds.

Full usage instructions land in this file at task H3.
