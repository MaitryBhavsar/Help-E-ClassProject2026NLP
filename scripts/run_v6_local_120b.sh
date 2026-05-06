#!/usr/bin/env bash
# Tiered routing for HELP-E v6 with gpt-oss-120b on local vLLM:
#   MAIN  (chatbot)  → local vLLM @ http://localhost:11436 serving openai/gpt-oss-120b
#   JUDGE (eval)     → same local vLLM @ :11436 (openai/gpt-oss-120b)
#   SIM   (user sim) → Lightning AI hosted gpt-oss-20b
#
# gpt-oss models emit hidden reasoning_content that counts against
# max_tokens, so MAX_TOKENS_BY_ROLE caps are bumped for MAIN-tier
# (and SIM session_context) via the env-var hooks in config.py.
#
# Output goes to output/local_v6_11436_120b/ — separate from any other
# v6 run.
#
# Usage:
#   scripts/run_v6_local_120b.sh --profile P01 --sessions 1 --turns 3
#   scripts/run_v6_local_120b.sh --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 2

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v6_local_120b] missing $REPO/.env.local (HELPE_SIM_API_KEY=<lightning sim key>)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v6_local_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN + JUDGE → local vLLM (gpt-oss-120b)
export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="openai/gpt-oss-120b"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="openai/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="EMPTY"

# SIM → Lightning AI gpt-oss-20b (same as default config)
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
# HELPE_SIM_API_KEY sourced from .env.local

# gpt-oss reasoning-content overhead → bump caps via the env-var hook
# in src/help_e/config.py (_max_tokens helper).
export HELPE_MAX_TOKENS_INFERENCE=4000          # default 2000
export HELPE_MAX_TOKENS_RECOMPUTE=3000           # default 1400
export HELPE_MAX_TOKENS_RESPONSE_V6=2500         # default 800
export HELPE_MAX_TOKENS_PERSONA_UPDATE_V6=3000   # default 1200
export HELPE_MAX_TOKENS_MITI_JUDGE=2000          # default 500 — judge is on 120b too now
export HELPE_MAX_TOKENS_ESC_JUDGE=2500           # default 700 — judge is on 120b too now

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_v6_11436_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_v6_11436_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_v6_11436_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on the local vLLM (chat-completion ping with enough tokens
# for gpt-oss-120b reasoning overhead).
echo "[run_v6_local_120b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v6_local_120b_preflight.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":200,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v6_local_120b] preflight FAILED: HTTP $http_code" >&2
    cat /tmp/run_v6_local_120b_preflight.json >&2
    echo >&2
    exit 1
fi
echo "[run_v6_local_120b] preflight OK"

echo "[run_v6_local_120b] launching v6 with: $*"
exec python -m help_e.run --system v6 "$@"
