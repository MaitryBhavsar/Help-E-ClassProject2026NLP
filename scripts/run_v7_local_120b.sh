#!/usr/bin/env bash
# Tiered routing for HELP-E v7 with gpt-oss-120b on local vLLM:
#   MAIN  (Agent 2 inference, Agent 5 response, Agent P persona)
#       → local vLLM @ http://localhost:11436 serving openai/gpt-oss-120b
#   JUDGE (miti_judge, esc_judge)
#       → same local vLLM @ :11436 (openai/gpt-oss-120b)
#   SIM   (mind1_v6, session_context, simulator)
#       → Lightning AI hosted gpt-oss-20b
#   SMALL (Agent 1, Agent X, Agent 3a, Agent 3b)
#       → Lightning AI hosted gpt-oss-20b (inherits from SIM endpoint
#         via SMALL_MODEL_URL/NAME defaults)
#
# Output goes to output/local_v7_11436_120b/ — separate from any v6 run.
#
# Usage:
#   scripts/run_v7_local_120b.sh --profile P01 --sessions 1 --turns 10
#   scripts/run_v7_local_120b.sh --all-profiles --sessions 3 --turns-list 20,10,10 --max-parallel-profiles 1

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v7_local_120b] missing $REPO/.env.local (HELPE_SIM_API_KEY=<lightning sim key>)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v7_local_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN + JUDGE → local vLLM (gpt-oss-120b on port 11436)
export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="openai/gpt-oss-120b"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="openai/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="EMPTY"

# SIM → Lightning AI gpt-oss-20b
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
# HELPE_SIM_API_KEY sourced from .env.local

# SMALL routing — explicitly point to the same Lightning AI gpt-oss-20b.
# (Defaults inherit SIM_OLLAMA_URL/SIM_MODEL via config.py, but setting
# explicitly avoids surprises if .env.local differs.)
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# gpt-oss reasoning-content overhead → bump v7 BIG-call caps via the
# env-var hook in src/help_e/config.py (_max_tokens helper).
export HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7=4000      # default 2000
export HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7=3000       # default 1600
export HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE=3000   # default 1500
export HELPE_MAX_TOKENS_MITI_JUDGE=2000               # default 500 — judge on 120b
export HELPE_MAX_TOKENS_ESC_JUDGE=2500                # default 700 — judge on 120b

# Reasoning-effort knob — gpt-oss models honor low/medium/high. "low"
# trims internal chain-of-thought so the 120b doesn't burn 2-3 minutes
# thinking before answering on extraction tasks. Override with `HELPE_REASONING_EFFORT=medium ./scripts/run_v7_local_120b.sh ...` if you
# need richer reasoning for a specific run.
export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# v7 SMALL agents stay at their normal caps (Lightning gpt-oss-20b).
# Override here only if smoke shows truncation.

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_v7_11436_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_v7_11436_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_v7_11436_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on the local vLLM.
echo "[run_v7_local_120b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_local_120b_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_local_120b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_v7_local_120b_preflight.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_local_120b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b.
echo "[run_v7_local_120b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_local_120b_sim_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_local_120b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_v7_local_120b_sim_preflight.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_local_120b] SIM preflight OK"

echo "[run_v7_local_120b] launching v7 with: $*"
exec python -m help_e.run --system v7 "$@"
