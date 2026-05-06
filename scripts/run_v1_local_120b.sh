#!/usr/bin/env bash
# V1 (history-only baseline, V7-aligned skeleton minus graph).
# Routing matches run_v3_local_120b.sh:
#   MAIN  + JUDGE → local vLLM @ http://localhost:11436 (gpt-oss-120b)
#   SIM   + SMALL → Lightning AI gpt-oss-20b
# Output: output/local_v1_11436_120b/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v1_local_120b] missing $REPO/.env.local" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v1_local_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="openai/gpt-oss-120b"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="openai/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="EMPTY"

export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"

export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

export HELPE_TRANSCRIPT_DIR="$REPO/output/local_v1_11436_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_v1_11436_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_v1_11436_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[run_v1_local_120b] launching v1 with: $*"
exec /usr/bin/python3 -m help_e.run --system v1 "$@"
