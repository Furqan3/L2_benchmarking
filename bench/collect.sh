#!/usr/bin/env bash
#
# One tick of unattended data collection (tasks F2, F3, C6).
#
# Designed to be run hourly from cron. Each tick does at most one submission
# and one resolve pass, then exits:
#
#     run_matrix --next   submits the single most-overdue matrix cell, and
#                         refuses any cell whose last repetition was less than
#                         min_gap_seconds ago. That refusal is the point - it
#                         is what keeps five repetitions from becoming five
#                         measurements of one moment.
#
#     resolve_run         fills in t2 and t3 for whatever has settled since
#                         the last tick. Safe to run repeatedly; it recomputes
#                         what is outstanding from the files each time.
#
# Nothing here loops. If a tick is missed the next one simply picks up the
# most overdue cell, and if the machine is asleep for six hours the matrix is
# six ticks behind rather than corrupted.
#
#     INSTALL
#
#     crontab -e   then add:
#     17 * * * * /home/unk/projects/l2_benchmarking/bench/collect.sh
#
# Minute 17 rather than 0: nothing else needs to be contended with, and it
# keeps the submissions off the top of the hour when public endpoints are
# busiest with everybody else's cron jobs.

set -uo pipefail

REPO="/home/unk/projects/l2_benchmarking"
PYTHON="/home/unk/.pyenv/versions/l2_bench/bin/python"
LOG="$REPO/bench/results/collect.log"

cd "$REPO" || exit 1
mkdir -p "$REPO/bench/results"

stamp() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

{
  echo "=== $(stamp) tick ==="

  # Submit at most one cell. A non-zero exit here is normal: it means nothing
  # was due yet, which is the gap doing its job.
  timeout 900 "$PYTHON" -m bench.run_matrix --next 2>&1 | tail -20

  # Then resolve whatever has settled. Separate from submission on purpose -
  # settlement takes tens of minutes on a ZK rollup and days on an optimistic
  # one, so it can never share a process with the thing that submits.
  timeout 1800 "$PYTHON" -m bench.resolve_run 2>&1 | tail -8

  echo "--- $(stamp) tick done ---"
} >> "$LOG" 2>&1
