#!/usr/bin/env bash
# Tiered routing for HELP-E v7 with EVERYTHING on Lightning AI:
#   MAIN  (Agent 2 inference, Agent 5 response, Agent P persona)
#       → Lightning AI lightning-ai/gpt-oss-120b
#   JUDGE (miti_judge, esc_judge)
#       → Lightning AI lightning-ai/gpt-oss-120b (same as MAIN)
#   SIM   (mind1_v6, session_context, simulator)
#       → Lightning AI lightning-ai/gpt-oss-20b
#   SMALL (Agent 1, Agent X, Agent 3a, Agent 3b)
#       → Lightning AI lightning-ai/gpt-oss-20b (inherits SIM defaults)
#
# Output goes to output/lightning_v7_120b/ — separate from the local
# v7 run.
#
# Usage:
#   scripts/run_v7_lightning_120b.sh --profile P01 --sessions 1 --turns 3
#   scripts/run_v7_lightning_120b.sh --profile P01 --sessions 3 --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v7_lightning_120b] missing $REPO/.env.local (HELPE_MAIN_API_KEY + HELPE_SIM_API_KEY)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v7_lightning_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# Lightning AI for MAIN (gpt-oss-120b). Use SIM key as fallback if MAIN
# key isn't set — common single-key setups.
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/gpt-oss-120b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:-${HELPE_SIM_API_KEY}}"

# JUDGE → same Lightning gpt-oss-120b
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

# SIM → Lightning gpt-oss-20b
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
# HELPE_SIM_API_KEY sourced from .env.local

# SMALL routing (v7 small agents)
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# gpt-oss reasoning-content overhead caps (same as local script).
export HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7=4000
export HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7=3000
export HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE=3000
export HELPE_MAX_TOKENS_MITI_JUDGE=2000
export HELPE_MAX_TOKENS_ESC_JUDGE=2500

# Reasoning-effort knob — gpt-oss honors low/medium/high. "low" trims
# CoT to recover wall-clock with minimal quality loss on extraction
# and persona-update workloads. Set to "" to leave it default.
export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_v7_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_v7_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_v7_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on Lightning gpt-oss-120b.
echo "[run_v7_lightning_120b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_lightning_120b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_lightning_120b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_v7_lightning_120b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_lightning_120b] MAIN preflight OK ($(jq -r '.choices[0].message.content // "<empty>"' /tmp/run_v7_lightning_120b_main.json 2>/dev/null || echo '?'))"

# Preflight on Lightning gpt-oss-20b.
echo "[run_v7_lightning_120b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_lightning_120b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_lightning_120b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_v7_lightning_120b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_lightning_120b] SIM preflight OK"

echo "[run_v7_lightning_120b] launching v7 with: $* (HELPE_REASONING_EFFORT=$HELPE_REASONING_EFFORT)"
exec python -m help_e.run --system v7 "$@"
