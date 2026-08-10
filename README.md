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

Mitacs Globalink 2026, project 50081, **scalability** track. Supervisor:
Prof. Sara Rouhani, TCDT Lab.

- Task breakdown: [`L2_Implementation_Handbook.pdf`](L2_Implementation_Handbook.pdf)
- What has been built and why: [`docs/L2_Benchmark_Project_Handbook.pdf`](docs/L2_Benchmark_Project_Handbook.pdf)

## Results so far

165 transactions, 100% success, across two rollup architectures.


| Level | zkSync Era Sepolia | kind | OP Sepolia | kind | ratio |
|---|---|---|---|---|---|
| Full trust (`t1`) | 18.07 s | observed | 14.97 s | observed | comparable |
| Partial trust (`t2`) | 25.49 min | observed | 1.99 min | *estimated* | OP **12.8x faster** |
| Trustless (`t3`) | 36.69 min | observed | **7.01 days** | *derived* | OP **275x slower** |

Same workload (native transfer), same batch size (50), both rollups.

At full trust the two architectures are **near-identical** — the sequencer
accepts in seconds either way. OP then pulls sharply ahead at partial trust,
because it posts batches to L1 far more often. And then it loses by more than
two orders of magnitude at trustless finality.

**That crossover is the result this project exists to produce**, and it is
precisely what a single-number latency benchmark cannot show: whichever rollup
you declare "faster" depends entirely on which trust assumption you meant.

The `kind` column is not decoration. zkSync's `t3` is *observed* — a proof was
verified in an Ethereum block whose timestamp we read, and the hash is in the
output. OP's is *derived*: nothing happens when a challenge window closes, so it
is a deadline computed from the output proposal plus the challenge period read
from the OptimismPortal. The two must never be tabulated as though they were the
same kind of measurement.

> **Caveat.** The zkSync figures come from 109 transactions but a **single
> batch**, submitted inside one eleven-minute window. Settlement therefore has
> n = 1 and the percentiles do not yet mean what percentiles normally mean.
> Task F2 — five repetitions at least an hour apart — is what fixes this.

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

Public RPC endpoints throttle, and Phase C queries wide block ranges. Override
any endpoint from `.env` with the **full URL**, not just the project id:

```sh
BENCH_RPC_SEPOLIA=https://sepolia.infura.io/v3/<project-id>
```

Deploying the workload token also needs a Solidity compiler, which
`py-solc-x` downloads on first use. No manual install required.

## Commands

Every command below is run from the repository root.

### Wallet

```sh
python -m bench.create_wallet      # generate the wallet; refuses to overwrite
python -m bench.core.wallet        # print the address loaded from .env
```

### Funding and connectivity

```sh
python -m bench.check_funds              # every network in the config
python -m bench.check_funds --priority   # only the networks this study needs
python -m bench.check_funds --record     # write balances into accounts.yaml
python -m bench.check_funds --faucets    # list faucets, grouped by gatekeeping
```

Reports chain ID, current block, balance and status per network, then prints the
specific next action for whatever is missing. Exit status is `0` when every
required network is funded and `1` otherwise, so it can gate a later script.

It also flags **stalled endpoints** — an RPC that answers correctly while serving
state frozen weeks in the past. That failure mode is invisible to a chain-ID
check and produces results that look entirely ordinary.

### Workloads

```sh
python -m bench.deploy_token -n zksync_sepolia --dry-run   # will the chain take it?
python -m bench.deploy_token -n zksync_sepolia --record    # deploy, write the address
python -m bench.check_workloads -n zksync_sepolia          # gas-estimate both workloads
python -m bench.check_workloads --all                      # every L2 in funding priority
```

`--dry-run` asks the node whether it will accept the bytecode at all, which is
how "does this rollup need its own compiler" gets answered by measurement.

### Running an experiment

```sh
python -m bench.submit_run -n zksync_sepolia -w native_transfer -c 50
python -m bench.submit_run -n op_sepolia -w erc20_transfer -c 5
python -m bench.submit_run -n op_sepolia -w native_transfer -c 1 --dry-run
```

The submit pass. Numbers a batch consecutively from one fetched nonce,
broadcasts in index order, polls every receipt round-robin, writes one JSON
object per transaction to `bench/results/<run_id>.jsonl`, and **exits as soon as
`t1` is known** — it never waits for settlement.

```sh
python -m bench.resolve_run                # one pass over every run file
python -m bench.resolve_run --run <run_id> # just one
python -m bench.resolve_run --watch 300    # repeat until nothing is outstanding
```

The resolve pass. Finds rows still missing `t2` or `t3`, asks the rollup where
they settled, writes the files back. Safe to run repeatedly and safe to
interrupt — it recomputes what is outstanding from disk every pass and holds
nothing between passes.

### Export and checks

```sh
python -m bench.export                  # raw CSV, summary, assumptions, comparison
python -m bench.export --eth-usd 4200   # override the configured ETH rate
python -m bench.check_config            # find config keys nothing reads
```

`export` writes four files to `bench/export/`: `raw_transactions.csv` (one row
per transaction, the primary artefact), `summary.csv` (derived, never a
replacement), `assumptions.txt` (the ETH rate, its source, and every declared
limitation) and `comparison.txt` (the cross-architecture table).

### Documentation

```sh
python docs/build_handbook.py    # regenerate the project handbook PDF
```

### Network keys

Accepted by `-n` / `--network`, defined in `bench/configs/networks.yaml`:

| Key | Network | Chain ID | Status |
|---|---|---|---|
| `sepolia` | Ethereum Sepolia | 11155111 | funded, L1 settlement layer |
| `zksync_sepolia` | zkSync Era Sepolia | 300 | funded, primary ZK target |
| `op_sepolia` | OP Sepolia | 11155420 | funded, optimistic target |
| `polygon_zkevm_cardona` | Polygon zkEVM Cardona | 2442 | **public RPC stalled** |
| `arbitrum_sepolia` | Arbitrum Sepolia | 421614 | unfunded, optional |

## Repository layout

```
bench/
  adapters/   one file per rollup - the only per-network code
  core/       submit, resolve, metrics - network-independent
  configs/    one YAML per concern; a run is described entirely by these
  abis/       committed contract ABIs
  contracts/  the workload token and its vendored OpenZeppelin sources
  results/    gitignored JSONL output
  export/     gitignored CSV deliverables
docs/         project handbook and its build script
```

The separation that matters is `core/` against `adapters/`. Everything in
`core/` works in terms of a `Settlement` record and never asks which rollup it is
talking to, so adding an architecture means adding one file to `adapters/`.

`.env`, `results/`, `export/` and the virtualenv are gitignored. Configs and ABIs
are committed deliberately: fetching an ABI at runtime would make results depend
on a third party staying online.

## Timeline

**Phases A–D are complete — the minimum viable deliverable.** E1 and E2 are done.

| Phase | | Status |
|---|---|---|
| A | Unblock | **Done** — A1–A5 |
| B | Make it real | **Done** — B1–B5 |
| C | The contribution | **Done** — C1–C6 |
| D | Make it defensible | **Done** — D1–D5 |
| E | The comparison | E1, E2 done; E3 in progress |
| F | Data collection | F1–F4 remaining |
| G | Analysis | G1–G5 remaining |
| H | Write and deliver | H1–H5 remaining |

### Immediate next steps

1. **E3** — resolve OP settlement across all rows, then run both rollups close
   together in time with the L1 gas price recorded at each.
2. **F2 and F3** — repetitions and the batch-size sweep. Start them early; they
   cost waiting rather than work, and F2 is what turns settlement from n = 1
   into a distribution.
3. **F4** — read-only mainnet observation. Costs nothing and grounds every
   testnet figure.
4. **H1** — the framework comparison table. No code, no data, no funds, so it is
   the task to work on whenever something else is blocked.

### Needs a human

- Replace the `PLACEHOLDER` ETH rate in `bench/configs/pricing.yaml` with one
  you actually looked up. Every dollar figure derives from it.
- Fill in the `faucet:` provenance fields in `bench/configs/accounts.yaml` — the
  report's experimental setup section needs them.
- Decide on Polygon zkEVM Cardona: replace the stalled RPC, or drop the network.
- An Etherscan API key, for F4. The V1 API is retired and V2 requires one.

## What this framework does not measure

- **Peak throughput.** Finding a sequencer's breaking point would degrade a
  service other people depend on, and would mostly measure our own rate limits.
  Achieved throughput at a stated submission rate is reported instead.
- **Proving time and cost.** Cited from published work, not measured — no GPU.
- **Per-transaction data-availability size.** A blob costs 131,072 bytes whether
  the rollup fills it or not, and a batch carries every user's transactions —
  batch 21623 held 875, of which 109 were ours. So DA bytes per transaction is a
  batch average that does not vary with workload, reported as
  `da_bytes_per_tx_batch_avg` and flagged `da_workload_sensitive=False` rather
  than dressed up as a per-workload measurement.
- **Observed trustless finality on an optimistic rollup.** Measured from the
  OptimismPortal itself, OP Sepolia's `proofMaturityDelaySeconds` is 604800 — a
  full seven days, the same as mainnet. Testnets are widely assumed to shorten
  their challenge windows; this one does not. Every `t3` on the optimistic side
  is therefore a computed deadline marked `derived`, and no amount of waiting
  would change that within the project.
- **L1 cost on OP Stack.** The number of transactions sharing a batch is not
  exposed without decoding the blob, so there is no denominator. Reported
  unavailable rather than divided by a guess.
- **Mainnet finality directly.** Read-only mainnet observation (F4) grounds the
  testnet figures against production behaviour.
