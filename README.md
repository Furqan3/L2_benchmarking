# L2_benchmarking

Measuring transaction finality on Ethereum Layer 2 rollups at three distinct
trust levels, and what it costs to reach each one.

| Level | Final when | Timestamp source |
|---|---|---|
| Full trust (`t1`) | the L2 sequencer accepts it | L2 RPC transaction receipt |
| Partial trust (`t2`) | its batch is posted to Ethereum | L1 batch-commit transaction |
| Trustless (`t3`) | its batch is proven, or the challenge window closes | a later L1 transaction |

Two of the three timestamps come from Ethereum rather than from the rollup. That
is the central design insight: the study is purely **observational**, measuring
public networks and hosting no infrastructure of its own.

Mitacs Globalink 2026, project 50081. Supervisor: Prof. Sara Rouhani, TCDT Lab.
Full task breakdown: [`L2_Implementation_Handbook.pdf`](L2_Implementation_Handbook.pdf).

## Setup

The project uses a pyenv virtualenv named `l2_bench` on Python 3.10.12.
`.python-version` activates it automatically on `cd` into the directory.

```sh
pyenv virtualenv 3.10.12 l2_bench
pyenv local l2_bench
pip install -r requirements.txt
```

Plain `venv` works equally well if you do not use pyenv:

```sh
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

Then create the test wallet:

```sh
python -m bench.create_wallet
```

This writes the private key to `.env` (gitignored, mode `600`) and the address to
`bench/configs/accounts.yaml` (committed, so every result is traceable to the
account that produced it). It refuses to overwrite an existing key.

> **This account is testnet-only and must never hold real funds.**

## Commands

Every command below is run from the repository root.

### Wallet

```sh
python -m bench.create_wallet      # generate the wallet; refuses to overwrite
python -m bench.core.wallet        # print the address loaded from .env
```

`bench.core.wallet` is the done-when test for A2: it loads the key from the
environment and fails loudly if the derived address disagrees with the one
recorded in `accounts.yaml`.

### Funding and connectivity

```sh
python -m bench.check_funds              # every network in the config
python -m bench.check_funds --priority   # only the networks this study needs
python -m bench.check_funds --record     # write balances into accounts.yaml
python -m bench.check_funds --faucets    # list faucets, grouped by gatekeeping
```

This is the done-when test for both A3 and A4 — it reports chain ID, current
block, balance and status per network, then prints the specific next action for
whatever is missing. Exit status is `0` when every required network is funded and
`1` otherwise, so it can gate a later script instead of being read by eye.

### Sending a transaction

```sh
python -m bench.send_one --dry-run            # build and sign locally, no broadcast
python -m bench.send_one                      # broadcast on Sepolia
python -m bench.send_one -n zksync_sepolia    # once the bridge has landed
python -m bench.send_one --value-wei 1000     # send a non-zero amount to ourselves
```

`--dry-run` needs no funds at all, since signing happens locally — the fastest
way to confirm the whole path is sound while waiting on a faucet or a bridge.
The default sends zero value to our own address, so nothing is spent but gas.

### Network keys

Accepted by `-n` / `--network`, defined in `bench/configs/networks.yaml`:

| Key | Network | Chain ID |
|---|---|---|
| `sepolia` | Ethereum Sepolia | 11155111 |
| `zksync_sepolia` | zkSync Era Sepolia | 300 |
| `polygon_zkevm_cardona` | Polygon zkEVM Cardona | 2442 |
| `op_sepolia` | OP Sepolia | 11155420 |
| `arbitrum_sepolia` | Arbitrum Sepolia | 421614 |

Funding priority is `sepolia` → `zksync_sepolia` → `op_sepolia`: one L1 source,
one ZK rollup and one optimistic rollup is the minimum for the cross-architecture
comparison in Phase E.

## Repository layout

```
bench/
  adapters/   one file per rollup - the only per-network code
  core/       submit, resolve, metrics - network-independent
  configs/    one YAML per experiment
  abis/       committed contract ABIs
  results/    gitignored output
  analysis/   notebooks and plotting
```

`.env`, `results/` and the virtualenv are gitignored. Configs and ABIs are
committed deliberately: fetching an ABI at runtime would make results depend on a
third party staying online.

## Timeline

Phase A is complete apart from bridging funds onto the L2s. Sepolia holds
**0.53 ETH**; all four L2 balances are currently zero, which blocks B2 onward.

### Phase A — Unblock

| Task | | Status |
|---|---|---|
| A1 | Clean repository | Done |
| A2 | Test wallet created and recorded | Done |
| A3 | Testnet funds | **In progress** — Sepolia funded; L2 bridges pending |
| A4 | Verify RPC endpoints | Done — all five reachable, chain IDs match |
| A5 | Send one transaction by hand | Done — confirmed on Sepolia |

### Phase B — Make it real

| Task | | Status |
|---|---|---|
| B1 | Workload contracts (native + ERC-20) | Remaining |
| B2 | Real submission — **the keystone** | Remaining |
| B3 | Nonce management under concurrency | Remaining |
| B4 | Full-trust finality `t1` | Remaining |
| B5 | Honest failure handling | Remaining |

### Phase C — The contribution

| Task | | Status |
|---|---|---|
| C1 | L1 contract addresses and ABIs | Remaining |
| C2 | Map L2 tx to its L1 settlement — **the crux** | Remaining |
| C3 | Partial-trust finality `t2` | Remaining |
| C4 | Trustless finality `t3` | Remaining |
| C5 | Independent interval measurement | Remaining |
| C6 | Two-pass submit/resolve runner | Remaining |

### Phase D — Make it defensible

| Task | | Status |
|---|---|---|
| D1 | Raw per-transaction export | Remaining |
| D2 | Real L1 costs from receipts | Remaining |
| D3 | Data-availability size (blob vs calldata) | Remaining |
| D4 | Honest achieved throughput | Remaining |
| D5 | Remove config that silently does nothing | Remaining |

**Phases A–D together are the minimum viable deliverable**: real transactions
with verifiable hashes, latency at three finality levels, cost from real L1 gas
with stated assumptions, and raw exported data.

### Phase E — The comparison

| Task | | Status |
|---|---|---|
| E1 | Optimistic rollup adapter | Remaining |
| E2 | Challenge-window finality model | Remaining |
| E3 | Cross-architecture comparison run | Remaining |

### Phase F — Data collection

| Task | | Status |
|---|---|---|
| F1 | Experiment matrix | Remaining |
| F2 | Repetitions (5+ per configuration) | Remaining |
| F3 | Batch-size sweep | Remaining |
| F4 | Read-only mainnet observation | Remaining |

### Phase G — Analysis

| Task | | Status |
|---|---|---|
| G1 | Latency CDFs | Remaining |
| G2 | Finality inversion figure — **the headline result** | Remaining |
| G3 | Cost breakdown | Remaining |
| G4 | Reproducibility check | Remaining |
| G5 | Threats to validity | Remaining |

### Phase H — Write and deliver

| Task | | Status |
|---|---|---|
| H1 | Framework comparison table (no dependencies) | Remaining |
| H2 | Report | Remaining |
| H3 | README a stranger can follow | Remaining |
| H4 | Presentation | Remaining |
| H5 | Handover notes | Remaining |

### Immediate next steps

1. Bridge Sepolia ETH to zkSync Era Sepolia — <https://portal.zksync.io/bridge/?network=sepolia> (~15 min)
2. Bridge Sepolia ETH to OP Sepolia — <https://app.optimism.io/bridge> (~15 min)
3. Confirm with `python -m bench.check_funds --priority --record`
4. Start B1/B2 — the keystone; nothing downstream exists until a real hash comes
   back from a real node

H1 needs no code, no data and no funds, so it is the task to work on whenever
something else is blocked.

## What this framework does not measure

- **Peak throughput.** Finding a sequencer's breaking point would degrade a
  service other people depend on, and would mostly measure our own rate limits.
  Achieved throughput at a stated submission rate is reported instead.
- **Proving time and cost.** Cited from published work, not measured — no GPU.
- **Mainnet finality directly.** Testnets prove more often and use drastically
  shortened challenge windows. Read-only mainnet observation (F4) grounds the
  testnet figures against production behaviour.
