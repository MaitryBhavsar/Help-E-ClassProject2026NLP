#!/usr/bin/env bash
# V1 (history-only baseline) on Fireworks AI — parallel-safe sibling of
# run_v1_local_120b.sh. All roles route to Fireworks.
# Output: output/fireworks_v1_120b/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.fireworks" ]; then
    echo "[run_v1_fireworks] missing $REPO/.env.fireworks" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.fireworks"
set +a

if [ -z "${HELPE_MAIN_API_KEY:-}" ]; then
    echo "[run_v1_fireworks] HELPE_MAIN_API_KEY not set after sourcing .env.fireworks" >&2
    exit 2
fi

export HELPE_MAIN_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_MAIN_MODEL="accounts/fireworks/models/gpt-oss-120b"

export HELPE_JUDGE_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_JUDGE_MODEL="accounts/fireworks/models/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

export HELPE_SIM_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_SIM_MODEL="accounts/fireworks/models/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_MAIN_API_KEY}"

export HELPE_SMALL_URL="https://api.fireworks.ai/inference"
export HELPE_SMALL_MODEL="accounts/fireworks/models/gpt-oss-20b"

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

export HELPE_TRANSCRIPT_DIR="$REPO/output/fireworks_v1_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/fireworks_v1_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/fireworks_v1_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[run_v1_fireworks] launching v1 on Fireworks with: $*"
exec /usr/bin/python3 -m help_e.run --system v1 "$@"
