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

**1,265 transactions, 97.9% success**, across two rollup architectures, plus
read-only observation of both chains' mainnets. 1,237 settled through `t3`
across **eight distinct zkSync batches**.


| Level | zkSync Era Sepolia | kind | OP Sepolia | kind | ratio |
|---|---|---|---|---|---|
| Full trust (`t1`) | 35.36 s | observed | 25.99 s | observed | comparable |
| Partial trust (`t2`) | 25.29 min | observed | 1.90 min | *estimated* | OP **13.3x faster** |
| Trustless (`t3`) | 35.82 min | observed | **7.01 days** | *derived* | OP **281x slower** |

Native transfer, medians over 487 and 323 successful transactions. **Snapshot as
of 08:30 PKT on 11 Aug 2026** — collection runs hourly and these move as it
accumulates. Regenerate with `python -m bench.export` and
`python -m bench.analysis.figures`.

At full trust the two architectures are **indistinguishable** — 18.90 s against
18.89 s. OP then pulls sharply ahead at partial trust, because it posts batches
to L1 every few minutes rather than every half hour. And then it loses by more
than two orders of magnitude at trustless finality.

**Watch the n before quoting a ratio.** The partial-trust figure read 13.4x,
then 44x an hour later, then settled at 13.3x once the sample reached 323 rows.
The 44x was a small-sample artefact.

### Reproducibility (G4)

Four cells have run on more than one day. Median divergence **12.1%**, worst
**65.7%** — but the split is the point: full trust reproduces to within 1% on
small batches, while partial and trustless move 20–66% between days. `t1`
measures a sequencer accepting a transaction; `t2` and `t3` depend on where in a
batch interval you happened to land.

### Batch-size scaling (F3)

Across eight settled batches of 617 to 1,737 transactions, per-transaction L1
cost falls **66%** and tracks 1/n closely. Note the x axis is the *rollup's*
batch size, not ours — our submission size does not move that denominator.

**That crossover is the result this project exists to produce**, and it is
precisely what a single-number latency benchmark cannot show: whichever rollup
you declare "faster" depends entirely on which trust assumption you meant.

The `kind` column is not decoration. zkSync's `t3` is *observed* — a proof was
verified in an Ethereum block whose timestamp we read, and the hash is in the
output. OP's is *derived*: nothing happens when a challenge window closes, so it
is a deadline computed from the output proposal plus the challenge period read
from the OptimismPortal. The two must never be tabulated as though they were the
same kind of measurement.

### Failures are reported, not hidden

27 of 709 transactions failed, and they are in the data with reasons:

| Count | Outcome | Reason |
|---|---|---|
| 24 | `timeout` | no receipt within 120 s |
| 3 | `rejected` | HTTP 429 from the public OP Sepolia endpoint |

Latency statistics count successes only, and the success rate is reported beside
them. The 429s are why read methods now retry with backoff — and why
`eth_sendRawTransaction` deliberately does **not**: retrying a broadcast risks
submitting the same signed transaction twice.

### Grounded against production (F4)

Read-only observation of both mainnets, sending nothing:

| | zkSync Era Mainnet | OP Mainnet |
|---|---|---|
| batch seal / posting interval | 30.15 min | 4.40 min |
| L1 commit interval | 31.88 min | — |
| commit → proof | **38.38 min** | — |
| output proposal interval | — | 60.20 min |
| proof maturity delay | — | **7.00 days** |

Two things follow. **Mainnet proves ~3.4x slower than the testnet** — 38.38 min
against 11.2 — so the testnet trustless figure understates production by a
stateable factor. And **OP Mainnet's challenge window is 7.00 days, identical to
OP Sepolia's**, which confirms against production that the testnet does not
shorten it. Testnets are widely assumed to; this one does not.

### Figures

`python -m bench.analysis.figures` writes PNG, PDF and a CSV table for each:

| Figure | Shows |
|---|---|
| `g1_latency_cdf` | Distributions per finality level, one panel per rollup, shared log axis |
| `g2_inversion` | **The headline** — the crossover, solid for observed and dashed for derived |
| `g3_cost_breakdown` | Where a batch's L1 cost goes, per transaction |

Cost split on zkSync batch 21623: **batch posting 74.00%, proof verification
24.83%, blob data availability 1.17%**. Blob DA being near-free is a property of
Sepolia's blob market sitting at its floor, not a mainnet result.

> **Caveat.** Every figure here is a snapshot from an ongoing collection. F2 is
> at 28 of 60 repetitions and accumulating hourly, so the medians still move.
> Regenerate before citing anything.

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

### The experiment matrix and unattended collection

```sh
python -m bench.run_matrix --plan                      # the checklist, and what is done
python -m bench.run_matrix --next                      # run the next due cell, then stop
python -m bench.run_matrix --paired -w native_transfer -c 50   # both rollups, back to back
bench/collect.sh                                       # one tick: submit + resolve
```

`--next` runs exactly **one** cell per invocation and refuses a repetition
sooner than 60 minutes after the last one for that cell. That refusal is the
point: five runs back to back measure one moment five times, which is what
repeating is meant to avoid.

`collect.sh` is the unattended tick, installed hourly via cron:

```
17 * * * * /home/unk/projects/l2_benchmarking/bench/collect.sh
```

Nothing loops. A missed tick means the next one picks up the most overdue cell;
a machine asleep for six hours leaves the matrix six ticks behind, not corrupted.

### Mainnet observation

```sh
python -m bench.observe_mainnet                  # both mainnet rollups
python -m bench.observe_mainnet -n zksync_mainnet --samples 40
```

Read-only. Mainnet networks carry `readonly: true` in `networks.yaml` and every
submitting entry point refuses them — this repository holds a funded key and
names chain 1, 10 and 324 beside the testnets, so one mistyped `-n` would
otherwise sign a transaction with real money.

### Figures

```sh
python -m bench.analysis.figures    # G1, G2, G3 as PNG + PDF, each with a CSV
```

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
  analysis/   figure generation; figures/ is gitignored output
  results/    gitignored JSONL output
  export/     gitignored CSV deliverables
  collect.sh  one unattended tick, run hourly from cron
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
| E | The comparison | **Done** — E1–E3 |
| F | Data collection | F1, F4 done; **F2 running hourly**, F3 partial |
| G | Analysis | G1–G3 done; G4, G5 remaining |
| H | Write and deliver | H1–H5 remaining |

**31 of 38 tasks complete.**

### What remains, and why

| Task | Blocked by |
|---|---|
| **F2** repetitions | Wall clock. 48 outstanding, one per hour by design — running them faster would measure one moment repeatedly |
| **F3** batch sweep | **Done** — answered via the rollup's own batch-size variation |
| **G4** reproducibility | **Done** — 12.1% median divergence between days |
| **G5** threats to validity | Nothing. Not yet written |
| **H1–H5** | You. H1 needs papers you have opened; H2/H4/H5 are yours to author |

**F3's cost half cannot be measured observationally.** Seven runs of sizes 1
through 50 all landed in zkSync batch 21623 alongside 875 other transactions,
giving an identical per-transaction cost every time. Per-transaction cost is
batch cost divided by the batch's own transaction count, and **we do not control
that count** — the rollup batches everyone's traffic on its own schedule. The
latency half is real and already visible. The honest version of the cost
question is per-transaction cost against the *rollup's* batch size across many
batches, which is what `observe_mainnet` reads.

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
- **The effect of our own batch size on cost.** Our submission size does not
  change the rollup's batch composition. Seven runs of sizes 1 to 50 all landed
  in one batch of 875 and cost exactly the same per transaction. What we vary is
  our load, not the rollup's.
- **Mainnet finality directly.** Read-only mainnet observation (F4) grounds the
  testnet figures against production behaviour.
