#!/usr/bin/env bash
# HELP-E v3 (HBM-free baseline) routed entirely to Lightning AI.
#
# v3 routing per the user's "everything except Agent 2 and Agent 5 on
# small model" directive:
#   MAIN  (Agent 2 inference + Agent 5 response)
#       → lightning-ai/llama-3.3-70b   (key: HELPE_MAIN_API_KEY)
#   JUDGE (miti_judge, esc_judge)
#       → lightning-ai/llama-3.3-70b   (same key)
#   SIM   (mind1_v6, session_context, simulator)
#       → lightning-ai/gpt-oss-20b     (key: HELPE_SIM_API_KEY from .env.local)
#   SMALL (Agent 1, Agent X, Agent 3 ProblemAgent, Agent 3c EdgeSummary,
#          Agent P persona update)
#       → lightning-ai/gpt-oss-20b     (inherits SIM)
#
# The new SMALL_MODEL_ROLES set in src/help_e/config.py is what causes
# agent3_problem_v3, agent3c_edge_summary, and agent_p_persona_update
# to route to gpt-oss-20b. Agent 2 (agent2_inference_v3) and Agent 5
# (agent5_response_v3) stay on llama-70b for inference quality and voice.
#
# Output: output/lightning_v3_70b/
#
# Usage:
#   scripts/run_v3_lightning_70b.sh --profile P18 --sessions 3 --turns-list 30,20,20
#   scripts/run_v3_lightning_70b.sh --profile P01 --profile P03 ... --turns-list 30,20,20 --max-parallel-profiles 4

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v3_lightning_70b] missing $REPO/.env.local (HELPE_SIM_API_KEY)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v3_lightning_70b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN → Lightning AI llama-3.3-70b (Agent 2 + Agent 5 only).
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

# JUDGE → same Lightning llama-3.3-70b.
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

# SIM → Lightning gpt-oss-20b on the maitry.bhavsar35@gmail.com tenant
# (same one v8 used). v7 currently runs on the dweeptrivedi tenant, so
# v3 here uses the maitry tenant to stay decoupled on the small
# endpoint while v7 finishes its tail batch.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing — same Lightning gpt-oss-20b as SIM.
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-70b doesn't emit reasoning_content separately, so the v3 BIG
# token caps (2000 for inference, 1600 for response) are fine without
# bumps. Clear any inherited gpt-oss-only env vars.
unset HELPE_REASONING_EFFORT

# gpt-oss-20b small-model token caps — same lesson as v7's bumps:
# reasoning_content eats budget, so each small role needs headroom.
# These match the v7 lightning script's caps.
export HELPE_MAX_TOKENS_AGENT1_USER_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3_PROBLEM_V3=2500
export HELPE_MAX_TOKENS_AGENT3C_EDGE_SUMMARY=2500
export HELPE_MAX_TOKENS_AGENTX_ROLLING_SUMMARY=2000
export HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE=3000
export HELPE_MAX_TOKENS_MIND1_V6=3000

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_v3_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_v3_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_v3_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on Lightning llama-3.3-70b.
echo "[run_v3_lightning_70b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v3_lightning_70b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v3_lightning_70b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_v3_lightning_70b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_v3_lightning_70b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b.
echo "[run_v3_lightning_70b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v3_lightning_70b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v3_lightning_70b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_v3_lightning_70b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_v3_lightning_70b] SIM preflight OK"

echo "[run_v3_lightning_70b] launching v3 with: $*"
exec python -m help_e.run --system v3 "$@"
