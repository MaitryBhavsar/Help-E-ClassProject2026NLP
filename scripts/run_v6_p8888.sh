#!/usr/bin/env bash
# Tiered routing for HELP-E v6 against the SSH-tunneled remote vLLM on :8888.
#   MAIN  (response_v6, inference, recompute, persona_update_v6)
#       → http://localhost:8888 (vLLM serving Llama-3.3-70B-Instruct)
#   JUDGE (miti_judge, esc_judge)
#       → http://localhost:8888 (same vLLM)
#   SIM   (mind1_v6, session_context)
#       → Lightning AI hosted gpt-oss-20b
#
# Output goes to output/local_8888_v6/... so this run cannot collide with any
# other v6 outputs (default-tree runs, run_v6_local on :11436, Fireworks runs).
#
# Usage:
#   scripts/run_v6_p8888.sh --profile P01 --sessions 1 --turns 3
#   scripts/run_v6_p8888.sh --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 3

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v6_p8888] missing $REPO/.env.local (HELPE_SIM_API_KEY=<lightning sim key>)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v6_p8888] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN + JUDGE → local vLLM tunnel @ :8888 (Llama-3.3-70B-Instruct)
export HELPE_MAIN_OLLAMA_URL="http://localhost:8888"
export HELPE_MAIN_MODEL="${HELPE_MAIN_MODEL:-meta-llama/Llama-3.3-70B-Instruct}"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:-EMPTY}"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:8888"
export HELPE_JUDGE_MODEL="${HELPE_JUDGE_MODEL:-meta-llama/Llama-3.3-70B-Instruct}"
export HELPE_JUDGE_API_KEY="${HELPE_JUDGE_API_KEY:-EMPTY}"

# SIM → Lightning AI gpt-oss-20b (HELPE_SIM_API_KEY sourced from .env.local)
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_8888_v6/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_8888_v6/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_8888_v6/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on the local vLLM (MAIN+JUDGE share the endpoint).
# Lightning AI SIM is skipped — it's known-working from the matrix.
echo "[run_v6_p8888] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v6_p8888_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":10,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v6_p8888] preflight FAILED: HTTP $http_code" >&2
    cat /tmp/run_v6_p8888_preflight.json >&2
    echo >&2
    echo "[run_v6_p8888] hint: try 'curl -s ${HELPE_MAIN_OLLAMA_URL}/v1/models' to see served model names" >&2
    exit 1
fi
echo "[run_v6_p8888] preflight OK"

echo "[run_v6_p8888] launching v6 with: $*"
exec python -m help_e.run --system v6 "$@"
