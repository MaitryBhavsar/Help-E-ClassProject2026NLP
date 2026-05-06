#!/usr/bin/env bash
# Tiered routing for HELP-E v6 against the SSH-tunneled remote vLLM on :9999
# serving openai/gpt-oss-120b.
#   MAIN  (response_v6, inference, recompute, persona_update_v6)
#       → http://localhost:9999 (vLLM serving openai/gpt-oss-120b)
#   JUDGE (miti_judge, esc_judge)
#       → http://localhost:9999 (same vLLM)
#   SIM   (mind1_v6, session_context)
#       → Lightning AI hosted gpt-oss-20b
#
# Output goes to output/local_9999_v6/... so this run cannot collide with any
# other v6 outputs (default-tree runs, run_v6_local on :11436,
# run_v6_p8888 on :8888, Fireworks runs).
#
# Usage:
#   scripts/run_v6_p9999.sh --profile P01 --sessions 1 --turns 3      # smoke
#   scripts/run_v6_p9999.sh --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 2

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v6_p9999] missing $REPO/.env.local (HELPE_SIM_API_KEY=<lightning sim key>)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v6_p9999] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN + JUDGE → local vLLM tunnel @ :9999 (openai/gpt-oss-120b)
export HELPE_MAIN_OLLAMA_URL="http://localhost:9999"
export HELPE_MAIN_MODEL="${HELPE_MAIN_MODEL:-openai/gpt-oss-120b}"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:-EMPTY}"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:9999"
export HELPE_JUDGE_MODEL="${HELPE_JUDGE_MODEL:-openai/gpt-oss-120b}"
export HELPE_JUDGE_API_KEY="${HELPE_JUDGE_API_KEY:-EMPTY}"

# SIM → Lightning AI gpt-oss-20b (HELPE_SIM_API_KEY sourced from .env.local)
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"

# gpt-oss models emit hidden `reasoning_content` that counts against
# max_tokens. Defaults in src/help_e/config.py were sized for non-
# reasoning Llama. With gpt-oss-120b on BOTH the MAIN and JUDGE tiers,
# every role on those tiers needs headroom for reasoning + content.
# (SIM tier session_context also bumped, since SIM is gpt-oss-20b.)
export HELPE_MAX_TOKENS_INFERENCE=8000           # default 2000 — bumped after P01/P02 hit
                                                  # JSON truncation (~7-8KB content + reasoning
                                                  # exceeded 4000-token cap on ~10% of turns)
export HELPE_MAX_TOKENS_RECOMPUTE=3000           # default 1400
export HELPE_MAX_TOKENS_RESPONSE_V6=2500         # default 800
export HELPE_MAX_TOKENS_PERSONA_UPDATE_V6=3000   # default 1200
export HELPE_MAX_TOKENS_MITI_JUDGE=2500          # default 500
export HELPE_MAX_TOKENS_ESC_JUDGE=2500           # default 700
export HELPE_MAX_TOKENS_SESSION_CONTEXT=1500     # default 500

# vLLM's gpt-oss-120b and Lightning's gpt-oss-20b both honor `reasoning_effort`.
# `high` was found to starve `content` of its token budget on the inference
# call (model burns max_tokens on hidden reasoning, emits empty content), so
# we use `medium` which lets the smoke-test-validated max_tokens above hold.
export HELPE_REASONING_EFFORT=medium

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_9999_v6/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_9999_v6/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_9999_v6/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on the local vLLM (MAIN+JUDGE share the endpoint).
# Lightning AI SIM is skipped — it's known-working from concurrent matrices.
echo "[run_v6_p9999] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v6_p9999_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v6_p9999] preflight FAILED: HTTP $http_code" >&2
    cat /tmp/run_v6_p9999_preflight.json >&2
    echo >&2
    echo "[run_v6_p9999] hint: try 'curl -s ${HELPE_MAIN_OLLAMA_URL}/v1/models' to see served model names" >&2
    exit 1
fi
echo "[run_v6_p9999] preflight OK"

echo "[run_v6_p9999] launching v6 with: $*"
exec python -m help_e.run --system v6 "$@"
