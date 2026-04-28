#!/usr/bin/env bash
# Launch HELP-E v6 against Fireworks.ai, isolated from any concurrent
# Lightning-AI-based runs.
#
# Routes ALL THREE endpoints (MAIN / SIM / JUDGE) to Fireworks for a
# single-provider run with no rate-limit competition. Output goes to a
# separate tree under output/fireworks_v6/ so it cannot collide with the
# default v6 outputs at src/help_e/transcripts/{profile}/v6/ and
# src/help_e/graphs_v6/v6/.
#
# Usage:
#   scripts/run_v6_fireworks.sh --profile P01 --sessions 1 --turns 3      # smoke
#   scripts/run_v6_fireworks.sh --profile P01 --sessions 4 --turns 10     # one-profile full
#   scripts/run_v6_fireworks.sh --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 4

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.fireworks" ]; then
    echo "[run_v6_fireworks] missing $REPO/.env.fireworks (HELPE_MAIN_API_KEY=fw_...)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.fireworks"
set +a

if [ -z "${HELPE_MAIN_API_KEY:-}" ]; then
    echo "[run_v6_fireworks] HELPE_MAIN_API_KEY not set after sourcing .env.fireworks" >&2
    exit 2
fi

# All three endpoints → Fireworks. URL must NOT end in /v1; the client
# (src/help_e/llm_client.py:_post_chat) appends /v1/chat/completions.
# Tiered routing on Fireworks:
#   MAIN  (chatbot: response_v6, inference, recompute, persona_update_v6) → gpt-oss-120b
#   SIM   (user simulator: mind1_v6, session_context)                      → gpt-oss-20b
#   JUDGE (miti_judge, esc_judge)                                          → llama-v3p3-70b-instruct
export HELPE_MAIN_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_MAIN_MODEL="accounts/fireworks/models/gpt-oss-120b"

export HELPE_SIM_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_SIM_MODEL="accounts/fireworks/models/gpt-oss-20b"
export HELPE_SIM_API_KEY="$HELPE_MAIN_API_KEY"

export HELPE_JUDGE_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_JUDGE_MODEL="accounts/fireworks/models/llama-v3p3-70b-instruct"
export HELPE_JUDGE_API_KEY="$HELPE_MAIN_API_KEY"

# gpt-oss models emit hidden `reasoning_content` that counts against
# max_tokens. Defaults in src/help_e/config.py were sized for non-
# reasoning Llama. Bump the MAIN-tier (gpt-oss-120b) roles + SIM-tier
# session_context (gpt-oss-20b) so reasoning + structured output fits.
# JUDGE-tier roles (llama-3.3-70b, no reasoning) keep their defaults.
export HELPE_MAX_TOKENS_INFERENCE=4000          # default 2000
export HELPE_MAX_TOKENS_RECOMPUTE=3000           # default 1400
export HELPE_MAX_TOKENS_RESPONSE_V6=2500         # default 800
export HELPE_MAX_TOKENS_PERSONA_UPDATE_V6=3000   # default 1200
export HELPE_MAX_TOKENS_SESSION_CONTEXT=1500     # default 500

# Isolated output tree — keeps this run from clobbering the default v6
# transcripts/graphs of any concurrent Lightning AI run.
export HELPE_TRANSCRIPT_DIR="$REPO/output/fireworks_v6/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/fireworks_v6/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/fireworks_v6/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight: chat-completions ping. We bypass scripts/check_endpoints.py
# because Fireworks's /v1/models listing doesn't surface every served
# model (e.g. llama-v3p3-70b-instruct is callable but not listed).
echo "[run_v6_fireworks] preflight (chat ping on $HELPE_MAIN_MODEL)..."
http_code=$(curl -sS -o /tmp/run_v6_fireworks_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v6_fireworks] preflight FAILED: HTTP $http_code" >&2
    cat /tmp/run_v6_fireworks_preflight.json >&2
    echo >&2
    exit 1
fi
echo "[run_v6_fireworks] preflight OK"

echo "[run_v6_fireworks] launching v6 with: $*"
exec python -m help_e.run --system v6 "$@"
