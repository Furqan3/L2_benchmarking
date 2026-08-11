"""Figures for the report (tasks G1, G2, G3).

    python -m bench.analysis.figures

Writes PNG and PDF into bench/analysis/figures/, and a CSV beside each one.
The CSV is not an afterthought: three of the palette slots sit below 3:1
contrast on a white surface, so every figure ships either direct labels or a
table view, and the numbers behind a figure should be readable without trusting
anyone's colour vision or printer.

    OBSERVED AND DERIVED ARE DRAWN DIFFERENTLY

A ZK rollup's trustless finality is a proof verified in an Ethereum block. An
optimistic rollup's is the moment a challenge window closes, and nothing happens
then - it is a deadline computed from the output proposal plus the challenge
period. Those are not the same kind of measurement and must not be drawn as the
same kind of line. Observed values are solid; derived values are dashed and
labelled. A reader who takes nothing else from the figure should still take
that.
"""

import csv
import statistics as st
from collections import defaultdict
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from bench.core.records import Outcome, iter_runs, load  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
FIGURE_DIR = REPO_ROOT / "bench" / "analysis" / "figures"

# Categorical slots 1-3 from the validated palette. Validated all-pairs in light
# mode: worst CVD dE 9.2, worst normal-vision dE 24.0. Assigned by identity and
# in fixed order - never by rank, never cycled.
SERIES = {
    "full trust": "#2a78d6",     # slot 1, blue
    "partial trust": "#eb6834",  # slot 2, orange
    "trustless": "#1baf7a",      # slot 3, aqua
}
ROLLUP_COLOR = {
    "zksync_sepolia": "#2a78d6",
    "op_sepolia": "#eb6834",
}
LABEL = {"zksync_sepolia": "zkSync Era", "op_sepolia": "OP Sepolia"}

INK = "#0b0b0b"
INK_SOFT = "#52514e"
GRID = "#e4e4e1"
SURFACE = "#fcfcfb"

LEVELS = [("t1", "full trust"), ("t2", "partial trust"), ("t3", "trustless")]


def style() -> None:
    plt.rcParams.update({
        "figure.facecolor": SURFACE,
        "axes.facecolor": SURFACE,
        "savefig.facecolor": SURFACE,
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "axes.edgecolor": GRID,
        "axes.labelcolor": INK_SOFT,
        "axes.titlesize": 11,
        "axes.titleweight": "normal",
        "axes.titlecolor": INK,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": GRID,
        "grid.linewidth": 0.7,
        "xtick.color": INK_SOFT,
        "ytick.color": INK_SOFT,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.frameon": False,
        "legend.fontsize": 8,
        "lines.linewidth": 2.0,
    })


def rows_by_network() -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = defaultdict(list)
    for path in iter_runs():
        for row in load(path):
            if row["outcome"] in Outcome.MEASURABLE:
                out[row["network"]].append(row)
    return out


def latencies(rows: list[dict], key: str) -> list[float]:
    return sorted(r[key] - r["t0"] for r in rows
                  if r.get(key) is not None and r.get("t0") is not None)


def is_derived(rows: list[dict], key: str) -> bool:
    return key == "t3" and any(r.get("t3_kind") == "derived" for r in rows)


def save(fig, name: str) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    for ext in ("png", "pdf"):
        fig.savefig(FIGURE_DIR / f"{name}.{ext}", dpi=200, bbox_inches="tight")
    plt.close(fig)
    print(f"  {name}.png / .pdf")


def write_table(name: str, header: list[str], rows: list[list]) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    with (FIGURE_DIR / f"{name}.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"  {name}.csv")


# --- G1 -------------------------------------------------------------------

def figure_cdf(data: dict[str, list[dict]]) -> None:
    """Cumulative distribution per finality level, one panel per rollup (G1).

    CDFs rather than bars of means: the tail is the interesting part of a
    latency distribution and a bar chart of means hides it entirely. The time
    axis is logarithmic because the three levels span seconds to days and will
    not share a linear axis.
    """
    networks = [k for k in ("zksync_sepolia", "op_sepolia") if data.get(k)]
    if not networks:
        return
    # Shared x as well as y. With independent x-scales the two panels look
    # comparable and are not - OP's trustless value is seven days and zkSync's
    # is half an hour, and separate axes would quietly hide that.
    fig, axes = plt.subplots(1, len(networks), figsize=(5.2 * len(networks), 3.9),
                             sharey=True, sharex=True)
    if len(networks) == 1:
        axes = [axes]

    table: list[list] = []
    for ax, network in zip(axes, networks):
        rows = data[network]
        for key, label in LEVELS:
            values = latencies(rows, key)
            if not values:
                continue
            derived = is_derived(rows, key)
            y = [(i + 1) / len(values) for i in range(len(values))]
            ax.step(values, y, where="post", color=SERIES[label],
                    linestyle="--" if derived else "-",
                    label=f"{label}{' (derived)' if derived else ''}")
            # Direct label at the median: the relief the palette's contrast
            # warning requires, and it saves a trip to the legend.
            #
            # Staggered vertically per level. Placing all three at the same
            # height overlapped them into unreadable mush wherever two medians
            # were close, which is most of the time on a log axis.
            mid = st.median(values)
            height = {"full trust": 0.66, "partial trust": 0.5,
                      "trustless": 0.34}[label]
            ax.annotate(label, xy=(mid, height), xytext=(0, 6),
                        textcoords="offset points", color=SERIES[label],
                        fontsize=7.5, ha="center", weight="bold",
                        annotation_clip=False)
            table.append([
                network, label, "derived" if derived else "observed", len(values),
                f"{values[0]:.2f}", f"{mid:.2f}",
                f"{values[min(len(values) - 1, int(len(values) * 0.95))]:.2f}",
                f"{values[-1]:.2f}",
            ])
        ax.set_xscale("log")
        ax.set_xlabel("latency from submission (s, log scale)")
        ax.set_title(LABEL.get(network, network))
        ax.set_ylim(0, 1.02)
        ax.spines[["top", "right"]].set_visible(False)

    axes[0].set_ylabel("fraction of transactions")
    # Legend below the panels rather than inside one: at these x-positions any
    # in-axes corner sits on top of a curve in one panel or the other.
    handles, labels_ = axes[0].get_legend_handles_labels()
    if len(handles) < 3:
        for ax in axes[1:]:
            h, l = ax.get_legend_handles_labels()
            for handle, name in zip(h, l):
                if name not in labels_:
                    handles.append(handle)
                    labels_.append(name)
    # The linestyle entry is not optional. Without it the legend shows a solid
    # "trustless" swatch while OP's trustless curve is dashed, and the single
    # most important distinction in the study - witnessed versus computed -
    # would be visible only to a reader who already knew to look for it.
    handles.append(Line2D([], [], color=INK_SOFT, linestyle="--"))
    labels_.append("dashed = derived, not observed")
    fig.legend(handles, labels_, loc="upper center",
               bbox_to_anchor=(0.5, 0.02), ncol=4)
    fig.suptitle("Finality latency is three distributions, not one number",
                 fontsize=12, color=INK, y=1.02, x=0.02, ha="left")
    save(fig, "g1_latency_cdf")
    write_table("g1_latency_cdf",
                ["network", "level", "kind", "n", "min_s", "median_s", "p95_s", "max_s"],
                table)


# --- G2 -------------------------------------------------------------------

def figure_inversion(data: dict[str, list[dict]]) -> None:
    """The headline: which rollup wins depends on the trust assumption (G2).

    Drawn as two lines across the three levels rather than grouped bars,
    because the claim is a crossover and a crossover is a shape. Ordered full
    trust -> partial trust -> trustless so it reads left to right.
    """
    networks = [k for k in ("zksync_sepolia", "op_sepolia") if data.get(k)]
    if len(networks) < 2:
        print("  g2 skipped - needs both architectures")
        return

    x = range(len(LEVELS))
    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    medians: dict[str, list[float]] = {}
    table: list[list] = []

    for network in networks:
        rows = data[network]
        series, styles = [], []
        for key, label in LEVELS:
            values = latencies(rows, key)
            series.append(st.median(values) if values else float("nan"))
            styles.append(is_derived(rows, key))
        medians[network] = series
        colour = ROLLUP_COLOR[network]

        # Solid through observed points; the final leg dashed where the value
        # is a computed deadline rather than a witnessed event.
        for i in range(len(series) - 1):
            ax.plot([i, i + 1], series[i:i + 2], color=colour,
                    linestyle="--" if styles[i + 1] else "-", zorder=3)
        ax.plot(list(x), series, "o", color=colour, markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, zorder=4)
        for i, (key, label) in enumerate(LEVELS):
            table.append([network, label,
                          "derived" if styles[i] else "observed",
                          f"{series[i]:.2f}"])

    # Direct labels go where the two series are furthest apart, not at a fixed
    # end: at full trust the rollups are within a few seconds of each other and
    # two labels there simply overlap, which is what the first draft did.
    if len(networks) == 2:
        first, second = medians[networks[0]], medians[networks[1]]
        gaps = [abs(first[i] - second[i]) / max(min(first[i], second[i]), 1e-9)
                for i in range(len(LEVELS))]
        at = gaps.index(max(gaps))
        for network in networks:
            value = medians[network][at]
            above = value >= medians[networks[0]][at] if network != networks[0] \
                else value >= medians[networks[1]][at]
            ax.annotate(LABEL[network], xy=(at, value),
                        xytext=(0, 11 if above else -17),
                        textcoords="offset points", ha="center",
                        color=ROLLUP_COLOR[network], fontsize=9, weight="bold")

    # Annotate the crossing, which is the entire point of the figure.
    if len(networks) == 2:
        a, b = medians[networks[0]], medians[networks[1]]
        for i in range(len(LEVELS) - 1):
            if (a[i] - b[i]) * (a[i + 1] - b[i + 1]) < 0:
                ax.axvspan(i, i + 1, color="#eda100", alpha=0.10, zorder=0)
                # Placed low in the band, clear of both lines: the previous
                # position put the caption straight through the dashed one.
                ax.annotate(
                    "the crossover:\nthe faster rollup swaps",
                    xy=(i + 0.5, min(min(a), min(b)) * 1.4),
                    ha="center", va="bottom", fontsize=8, color=INK_SOFT,
                )
                break

    ax.set_yscale("log")
    ax.set_xticks(list(x))
    ax.set_xticklabels([f"{label}\n(t{i + 1})" for i, (_k, label) in enumerate(LEVELS)])
    ax.set_ylabel("median latency from submission (s, log scale)")
    ax.set_xlim(-0.55, len(LEVELS) - 0.45)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", visible=False)

    # Reference lines labelled at the left, where the panel is empty. At the
    # right they crowded the final data point.
    for seconds, name in ((60, "1 min"), (3600, "1 hour"), (86400, "1 day")):
        ax.axhline(seconds, color=GRID, linewidth=0.8, zorder=0)
        ax.annotate(name, xy=(-0.5, seconds), xytext=(0, 3),
                    textcoords="offset points", ha="left", fontsize=7,
                    color=INK_SOFT)

    ax.legend(handles=[
        Line2D([], [], color=INK_SOFT, linestyle="-", label="observed"),
        Line2D([], [], color=INK_SOFT, linestyle="--", label="derived (a deadline, not an event)"),
    ], loc="upper left")

    fig.suptitle("An optimistic rollup settles sooner and finalises far later",
                 fontsize=12, color=INK, y=1.0, x=0.02, ha="left")
    save(fig, "g2_inversion")
    write_table("g2_inversion", ["network", "level", "kind", "median_s"], table)


# --- G3 -------------------------------------------------------------------

def figure_cost(data: dict[str, list[dict]]) -> None:
    """Where the L1 cost of a batch actually goes (G3).

    Split three ways - blob data availability, batch-posting execution, and
    proof verification - because those are the terms that behave differently
    as batch size grows, and lumping them hides which one dominates.

        WHY ONLY THE ZK ROLLUP APPEARS

    An earlier version put OP Stack batches beside zkSync's. That comparison
    was meaningless: one OP posting covers a few minutes of that chain's whole
    traffic, one zkSync batch covered 875 transactions, and neither number is
    the other's unit. Since OP exposes no batch transaction count, its cost has
    no denominator and cannot join a per-transaction breakdown at all. It is
    excluded and the figure says so, rather than being drawn as a shorter bar
    that invites the comparison anyway.
    """
    from bench.core.costs import l1_cost
    from bench.core.networks import connect_verified, load_networks

    networks = load_networks()

    # Only batches whose composition is fully known. A batch without a
    # transaction count cannot be costed per transaction, and a batch without a
    # proof has not finished settling.
    batches: dict[tuple, dict] = {}
    excluded: set[str] = set()
    for network, rows in data.items():
        for row in rows:
            if not row.get("l1_commit_tx") or row.get("batch") is None:
                continue
            if not row.get("batch_tx_count"):
                excluded.add(network)
                continue
            batches.setdefault((network, row["batch"]), row)

    if not batches:
        print("  g3 skipped - no batch with a known transaction count")
        return

    connections: dict[str, object] = {}
    labels, da, execution, proof, per_tx = [], [], [], [], []
    table: list[list] = []

    for (network, batch), row in sorted(batches.items(), key=lambda kv: kv[0][1]):
        net = networks.get(network)
        if not net or not net.settles_on:
            continue
        if net.settles_on not in connections:
            connections[net.settles_on] = connect_verified(networks[net.settles_on])
        w3 = connections[net.settles_on]
        try:
            commit = l1_cost(w3, row["l1_commit_tx"])
        except Exception:  # noqa: BLE001
            continue
        prove_wei = 0
        if row.get("l1_prove_tx"):
            try:
                prove_wei = l1_cost(w3, row["l1_prove_tx"]).total_wei
            except Exception:  # noqa: BLE001
                prove_wei = 0

        count = row["batch_tx_count"]
        labels.append(f"{LABEL.get(network, network)}\nbatch {batch}\n{count:,} txs")
        # Nano-ETH. At these magnitudes an ETH axis renders as "1e-7" and a
        # reader has to decode the offset before reading the chart.
        da.append(commit.blob_wei / count / 1e9)
        execution.append(commit.execution_wei / count / 1e9)
        proof.append(prove_wei / count / 1e9)
        per_tx.append((commit.total_wei + prove_wei) / count / 1e9)
        table.append([network, batch, count,
                      f"{commit.blob_wei / count / 1e18:.12f}",
                      f"{commit.execution_wei / count / 1e18:.12f}",
                      f"{prove_wei / count / 1e18:.12f}",
                      commit.da_kind, commit.da_bytes])

    if not labels:
        print("  g3 skipped - no costable batch")
        return

    fig, ax = plt.subplots(figsize=(max(4.6, 2.1 * len(labels) + 2.6), 4.2))
    x = range(len(labels))
    width = 0.42
    # 2px surface gap between stacked segments, per the mark spec.
    bars = [
        ("blob data availability", da, "#2a78d6", [0.0] * len(da)),
        ("batch posting (execution)", execution, "#eb6834", da),
        ("proof verification", proof, "#1baf7a",
         [a + b for a, b in zip(da, execution)]),
    ]
    for name, values, colour, bottom in bars:
        ax.bar(x, values, bottom=bottom, color=colour, label=name,
               width=width, edgecolor=SURFACE, linewidth=2)

    for i, total in enumerate(per_tx):
        ax.annotate(f"{total:.1f} nETH/tx", xy=(i, total), xytext=(0, 6),
                    textcoords="offset points", ha="center", fontsize=8,
                    color=INK, weight="bold")

    ax.set_xticks(list(x))
    ax.set_xticklabels(labels, fontsize=8)
    ax.set_ylabel("L1 cost per transaction (nanoETH)")
    ax.set_ylim(0, max(per_tx) * 1.28)
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper left", bbox_to_anchor=(0.0, -0.16), ncol=1)

    if excluded:
        names = ", ".join(sorted(LABEL.get(n, n) for n in excluded))
        ax.annotate(
            f"{names} excluded: batch transaction count is not exposed,\n"
            "so its L1 cost has no per-transaction denominator.",
            xy=(0.99, 0.97), xycoords="axes fraction", ha="right", va="top",
            fontsize=7.5, color=INK_SOFT, style="italic")

    # The caption is a claim, per G2 step 4 - but derived from the data rather
    # than typed, so it cannot come to contradict the chart it sits above. The
    # first draft asserted that data availability dominates while the DA
    # segment was too small to see.
    totals = {"data availability": sum(da), "batch posting": sum(execution),
              "proof verification": sum(proof)}
    leader = max(totals, key=lambda k: totals[k])
    share = totals[leader] / sum(totals.values()) * 100 if sum(totals.values()) else 0
    smallest = min(totals, key=lambda k: totals[k])
    small_share = (totals[smallest] / sum(totals.values()) * 100
                   if sum(totals.values()) else 0)
    fig.suptitle(
        f"{leader.capitalize()} is {share:.0f}% of settlement cost; "
        f"{smallest} is {small_share:.2f}%",
        fontsize=12, color=INK, y=1.0, x=0.02, ha="left")
    save(fig, "g3_cost_breakdown")
    write_table("g3_cost_breakdown",
                ["network", "batch", "batch_tx_count", "da_eth_per_tx",
                 "execution_eth_per_tx", "proof_eth_per_tx", "da_kind", "da_bytes"],
                table)


def figure_batch_scaling(data: dict[str, list[dict]]) -> None:
    """Per-transaction cost against the batch's own size (F3, honestly).

        WHY THE X AXIS IS THE ROLLUP'S BATCH SIZE AND NOT OURS

    The task asks whether per-transaction cost falls as batch size grows. The
    obvious experiment - submit 1, then 10, then 100, and compare - does not
    answer it. Seven runs of sizes 1 to 50 all landed in one batch of 875 and
    produced an identical cost, because per-transaction cost is the batch's
    cost over the batch's own transaction count and we are a small minority of
    that count. Our submission size is our load, not the rollup's batch size.

    What does answer it is the rollup's own variation. Its batches differ in
    size for reasons of its own - traffic, timing, sealing rules - and across
    enough of them the relationship is visible without us having caused it.
    Observation rather than intervention, which is the whole design of this
    study.
    """
    from bench.core.costs import l1_cost
    from bench.core.networks import connect_verified, load_networks

    networks = load_networks()
    seen: dict[tuple, dict] = {}
    for network, rows in data.items():
        for row in rows:
            if (row.get("batch") is not None and row.get("batch_tx_count")
                    and row.get("l1_commit_tx")):
                seen.setdefault((network, row["batch"]), row)
    if len(seen) < 3:
        print("  f3 skipped - needs at least three settled batches")
        return

    connections: dict[str, object] = {}
    points: list[tuple[int, float, str]] = []
    for (network, batch), row in sorted(seen.items(), key=lambda kv: kv[0][1]):
        net = networks.get(network)
        if not net or not net.settles_on:
            continue
        if net.settles_on not in connections:
            connections[net.settles_on] = connect_verified(networks[net.settles_on])
        w3 = connections[net.settles_on]
        try:
            total = l1_cost(w3, row["l1_commit_tx"]).total_wei
            if row.get("l1_prove_tx"):
                total += l1_cost(w3, row["l1_prove_tx"]).total_wei
        except Exception:  # noqa: BLE001
            continue
        points.append((row["batch_tx_count"], total / row["batch_tx_count"] / 1e9,
                       str(batch)))
    if len(points) < 3:
        print("  f3 skipped - too few costable batches")
        return

    points.sort()
    sizes = [p[0] for p in points]
    costs = [p[1] for p in points]

    fig, ax = plt.subplots(figsize=(6.4, 4.2))
    ax.plot(sizes, costs, "o", color="#2a78d6", markersize=9,
            markeredgecolor=SURFACE, markeredgewidth=2, zorder=3)

    # The reference curve is not a fit. If the batch's fixed cost were spread
    # evenly, cost per transaction would go exactly as 1/n; drawing that shows
    # how closely the observed points follow it without implying we modelled
    # anything.
    anchor = sizes[0] * costs[0]
    curve_x = list(range(min(sizes), max(sizes) + 1, max(1, (max(sizes) - min(sizes)) // 60)))
    ax.plot(curve_x, [anchor / x for x in curve_x], "-", color=INK_SOFT,
            linewidth=1.2, alpha=0.55, zorder=1,
            label="a fixed batch cost shared evenly (1/n)")

    # Alternate the label above and below when two batches sit close together
    # on the x axis; at a fixed offset 21626 and 21628 overlapped into "2162826".
    span = (max(sizes) - min(sizes)) or 1
    previous = None
    flip = False
    for size, cost, label in points:
        if previous is not None and (size - previous) / span < 0.06:
            flip = not flip
        else:
            flip = False
        ax.annotate(label, xy=(size, cost), xytext=(0, -18 if flip else 11),
                    textcoords="offset points", ha="center", fontsize=7,
                    color=INK_SOFT)
        previous = size

    ax.set_xlabel("transactions in the batch (the rollup's, not ours)")
    ax.set_ylabel("L1 cost per transaction (nanoETH)")
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(loc="upper right")

    drop = (costs[0] - costs[-1]) / costs[0] * 100
    fig.suptitle(
        f"Cost per transaction falls {drop:.0f}% from the smallest batch to "
        f"the largest",
        fontsize=12, color=INK, y=1.0, x=0.02, ha="left")
    save(fig, "f3_batch_scaling")
    write_table("f3_batch_scaling", ["batch", "batch_tx_count", "cost_neth_per_tx"],
                [[c, a, f"{b:.2f}"] for a, b, c in points])


def main() -> int:
    style()
    data = rows_by_network()
    if not data:
        print("no rows in bench/results/")
        return 1
    print(f"\n{sum(len(v) for v in data.values())} successful transactions "
          f"across {len(data)} network(s)\n")
    figure_cdf(data)
    figure_inversion(data)
    figure_cost(data)
    figure_batch_scaling(data)
    print(f"\nfigures in {FIGURE_DIR}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
