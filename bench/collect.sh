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
LOCK="/tmp/l2_benchmark_collect.lock"
PYTHON="/home/unk/.pyenv/versions/l2_bench/bin/python"
LOG="$REPO/bench/results/collect.log"

cd "$REPO" || exit 1
mkdir -p "$REPO/bench/results"

# Only one tick at a time. The jitter below can push a tick close to the next
# hour, and two submitters against one public endpoint is how the first HTTP
# 429 happened.
exec 9>"$LOCK"
flock -n 9 || { echo "$(date -u +%FT%TZ) tick skipped - one already running" >> "$REPO/bench/results/collect.log"; exit 0; }

# Wait a random part of the hour before submitting.
#
#     WHY THIS MATTERS MORE THAN IT LOOKS
#
# Cron fires on a fixed minute. zkSync Sepolia commits its batches every 120
# minutes. An hourly job at a fixed minute therefore only ever lands in TWO
# points of that cycle, and the round-robin assigns cells to whichever hour
# comes next - so which workload a cell holds became correlated with where in
# the batch interval it was submitted.
#
# Measured over 1,269 settled zkSync rows: even-hour submissions waited a median
# of 25.5 minutes for their batch, odd-hour submissions 87.2. That is not a
# property of anything being measured. It made ERC-20 transactions appear to
# settle 3.3x slower than native ones, which is impossible - a rollup does not
# consult the workload when deciding to post a batch.
#
# A uniform delay across the hour decorrelates submission time from the clock,
# so the batch interval is sampled evenly whatever its period.
JITTER=$(( RANDOM % 3000 ))
echo "$(date -u +%FT%TZ) tick waiting ${JITTER}s before submitting" >> "$REPO/bench/results/collect.log"
sleep "$JITTER"

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

  # Then check the result against invariants that cannot legitimately fail.
  #
  # This is the tick's whole reason for existing unattended. Both data bugs
  # this project has had produced plausible numbers rather than crashes, and
  # both were caught by somebody happening to look at a median. Running the
  # check every hour means the next one is caught by the machine, at the tick
  # it appears, rather than in week seven by a reviewer.
  if ! timeout 600 "$PYTHON" -m bench.check_data --quiet 2>&1 | tail -20; then
    echo "!!! DATA INTEGRITY FAILURE - see above. Collection continues, but"
    echo "!!! do not trust figures generated from these rows until it is fixed."
  fi

  echo "--- $(stamp) tick done ---"
} >> "$LOG" 2>&1
