#!/usr/bin/env bash
# Day-4 launcher for the v1..v5 ablation matrix (§14 Day 4).
#
# Usage:
#   scripts/run_all.sh                     # full 30-profile × 4-session matrix, all 5 systems
#   scripts/run_all.sh --smoke             # 1 profile, 1 session × 3 turns, v3 only
#   scripts/run_all.sh --systems v4 v5     # subset of systems
#   scripts/run_all.sh --profile P01       # specific profile id (repeatable)
#   scripts/run_all.sh --sessions 2 --turns 6
#   scripts/run_all.sh --run-judge         # run E1 judge inline (otherwise post-hoc)
#   scripts/run_all.sh --parallel 3        # launch up to N systems concurrently
#
# One log file per (system) under $LOG_DIR. Per-profile errors are captured by
# run.py (exit code 1) and don't abort the launcher. Transport failures against
# the Ollama endpoint bubble up through LLMStructuredError; the matrix keeps
# going to the next profile.

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
SRC="$REPO/src"

ALL_SYSTEMS=(v1 v2 v3 v4 v5)
SYSTEMS=("${ALL_SYSTEMS[@]}")
PROFILE_ARGS=()
USE_ALL_PROFILES=1
SESSIONS=4
TURNS=10
RUN_JUDGE=0
PARALLEL=1
LOG_LEVEL=INFO
SMOKE=0

LOG_DIR="${HELPE_LAUNCHER_LOG_DIR:-$REPO/output/logs/run_all_$(date +%Y%m%d_%H%M%S)}"

usage() {
    sed -n '2,24p' "$0"
    exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --smoke)       SMOKE=1; shift ;;
        --systems)     shift; SYSTEMS=(); while [[ $# -gt 0 && ! "$1" =~ ^-- ]]; do SYSTEMS+=("$1"); shift; done ;;
        --profile)     shift; PROFILE_ARGS+=(--profile "$1"); USE_ALL_PROFILES=0; shift ;;
        --sessions)    shift; SESSIONS="$1"; shift ;;
        --turns)       shift; TURNS="$1"; shift ;;
        --run-judge)   RUN_JUDGE=1; shift ;;
        --parallel)    shift; PARALLEL="$1"; shift ;;
        --log-level)   shift; LOG_LEVEL="$1"; shift ;;
        --log-dir)     shift; LOG_DIR="$1"; shift ;;
        -h|--help)     usage ;;
        *) echo "unknown arg: $1" >&2; usage 1 ;;
    esac
done

if [[ $SMOKE -eq 1 ]]; then
    SYSTEMS=(v3)
    SESSIONS=1
    TURNS=3
    if [[ $USE_ALL_PROFILES -eq 1 ]]; then
        # Pick the first profile available; error if none.
        first_profile="$(ls "$SRC/help_e/data/profiles/"*.yaml 2>/dev/null | head -n1 | xargs -n1 basename 2>/dev/null | sed 's/\.yaml$//')"
        if [[ -z "$first_profile" ]]; then
            echo "no profiles seeded under src/help_e/data/profiles/. run seeding first (task #31)." >&2
            exit 2
        fi
        PROFILE_ARGS=(--profile "$first_profile")
        USE_ALL_PROFILES=0
    fi
fi

if [[ $USE_ALL_PROFILES -eq 1 ]]; then
    PROFILE_ARGS=(--all-profiles)
fi

JUDGE_FLAG=()
if [[ $RUN_JUDGE -eq 1 ]]; then
    JUDGE_FLAG=(--run-judge)
fi

mkdir -p "$LOG_DIR"
echo "launcher: log_dir=$LOG_DIR"
echo "launcher: systems=${SYSTEMS[*]}  sessions=$SESSIONS  turns=$TURNS  parallel=$PARALLEL"
echo "launcher: profiles=${PROFILE_ARGS[*]}"

cd "$REPO"
export PYTHONPATH="$SRC:${PYTHONPATH:-}"

pids=()
statuses=()
launched=()

wait_for_slot() {
    # Block until fewer than PARALLEL children are running.
    while [[ ${#pids[@]} -ge $PARALLEL ]]; do
        for i in "${!pids[@]}"; do
            if ! kill -0 "${pids[$i]}" 2>/dev/null; then
                wait "${pids[$i]}" 2>/dev/null
                rc=$?
                statuses+=("${launched[$i]}:$rc")
                unset 'pids[$i]'
                unset 'launched[$i]'
                pids=("${pids[@]}")
                launched=("${launched[@]}")
                break
            fi
        done
        sleep 1
    done
}

for system in "${SYSTEMS[@]}"; do
    wait_for_slot
    log_file="$LOG_DIR/${system}.log"
    echo "launching $system -> $log_file"
    (
        python -m help_e.run \
            --system "$system" \
            "${PROFILE_ARGS[@]}" \
            --sessions "$SESSIONS" \
            --turns "$TURNS" \
            --log-level "$LOG_LEVEL" \
            ${JUDGE_FLAG[@]+"${JUDGE_FLAG[@]}"} \
            > "$log_file" 2>&1
    ) &
    pids+=($!)
    launched+=("$system")
done

# Drain remaining.
for i in "${!pids[@]}"; do
    wait "${pids[$i]}"
    rc=$?
    statuses+=("${launched[$i]}:$rc")
done

echo
echo "=== run_all results ==="
fail=0
for s in "${statuses[@]}"; do
    name="${s%:*}"
    code="${s##*:}"
    case "$code" in
        0) echo "  $name: ok" ;;
        1) echo "  $name: partial (some profiles failed — check log)"; fail=1 ;;
        *) echo "  $name: FAIL (exit $code)"; fail=1 ;;
    esac
done
echo "logs: $LOG_DIR"
exit "$fail"
