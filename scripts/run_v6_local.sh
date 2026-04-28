#!/usr/bin/env bash
# Tiered routing for HELP-E v6:
#   MAIN  (chatbot: response_v6, inference, recompute, persona_update_v6)
#       → local vLLM @ http://localhost:11436 serving Llama-3.3-70B-Instruct
#   JUDGE (miti_judge, esc_judge)
#       → same local vLLM @ :11436 (Llama-3.3-70B-Instruct)
#   SIM   (mind1_v6, session_context)
#       → Lightning AI hosted gpt-oss-20b
#
# Output goes to output/local_v6/... so this run cannot collide with any
# other v6 outputs (Fireworks runs, default-config matrix runs).
#
# Usage:
#   scripts/run_v6_local.sh --profile P01 --sessions 1 --turns 3
#   scripts/run_v6_local.sh --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 4

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v6_local] missing $REPO/.env.local (HELPE_SIM_API_KEY=<lightning sim key>)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v6_local] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN + JUDGE → local vLLM (Llama-3.3-70B-Instruct)
export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="meta-llama/Llama-3.3-70B-Instruct"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="meta-llama/Llama-3.3-70B-Instruct"
export HELPE_JUDGE_API_KEY="EMPTY"

# SIM → Lightning AI gpt-oss-20b (known-working from the running matrix)
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
# HELPE_SIM_API_KEY sourced from .env.local

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_v6/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_v6/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_v6/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on the local vLLM (MAIN+JUDGE share the endpoint).
# Lightning AI SIM is skipped — it's known-working from the matrix.
echo "[run_v6_local] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v6_local_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":10,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v6_local] preflight FAILED: HTTP $http_code" >&2
    cat /tmp/run_v6_local_preflight.json >&2
    echo >&2
    exit 1
fi
echo "[run_v6_local] preflight OK"

echo "[run_v6_local] launching v6 with: $*"
exec python -m help_e.run --system v6 "$@"
