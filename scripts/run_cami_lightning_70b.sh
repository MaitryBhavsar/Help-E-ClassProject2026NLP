#!/usr/bin/env bash
# CAMI baseline (vendored under external/CAMI) routed entirely to
# Lightning AI — matches run_v7_lightning_70b.sh / run_v8_lightning_70b.sh
# on EVERY tier (counselor, judge, simulator), so CAMI vs v7/v8 is a
# clean head-to-head: same MAIN model, same JUDGE model, same SIM model.
#   MAIN  + JUDGE → lightning-ai/llama-3.3-70b   (HELPE_MAIN_API_KEY)
#   SIM   + SMALL → lightning-ai/gpt-oss-20b     (CAMI tenant)
# Output: output/lightning_cami_70b/
#
# CAMI uses the OpenAI Python SDK directly (separate from HELP-E's LLM
# client), so we ALSO export OPENAI_API_KEY + OPENAI_BASE_URL pointing
# at the same Lightning endpoint that HELP-E's MAIN endpoint uses.
#
# Usage:
#   scripts/run_cami_lightning_70b.sh --profile P01 --sessions 1 --turns 5
#   scripts/run_cami_lightning_70b.sh --profile P01 --turns-list 30,20,20
#   scripts/run_cami_lightning_70b.sh --all-profiles --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# .env.local supplies HELPE_SIM_API_KEY (gpt-oss-20b tenant). The MAIN
# key falls back to the same Lightning AI key v7_lightning_70b.sh uses
# for llama-3.3-70b. Override by exporting HELPE_MAIN_API_KEY before
# launch.
if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_cami_lightning_70b] missing $REPO/.env.local" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

# MAIN → Lightning AI llama-3.3-70b (large LLM calls).
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

# JUDGE → same Lightning llama-3.3-70b (same tenant as MAIN — matches
# v7/v8_lightning_70b.sh exactly).
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

# SIM → Lightning gpt-oss-20b on the CAMI tenant (key set per user
# directive 2026-05-05). Separate from v7's tenant (9c9c9b98...) and
# v8's tenant (780abb93...) so concurrent CAMI runs don't compete with
# in-flight v7/v8 work for rate-limit budget.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing — same Lightning gpt-oss-20b tenant as SIM. (CAMI does
# not use HELP-E's SMALL roles, but unset/wrong values can leak into
# HELP-E session-context generation, so set explicitly.)
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-3.3-70b is a non-CoT model: no reasoning_content overhead, so
# the gpt-oss-style token bumps are unnecessary on the MAIN/JUDGE side.
unset HELPE_REASONING_EFFORT
unset HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7
unset HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7
unset HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE
unset HELPE_MAX_TOKENS_MITI_JUDGE
unset HELPE_MAX_TOKENS_ESC_JUDGE

# gpt-oss-20b on Lightning DOES emit reasoning_content and benefits
# from larger ceilings on the SMALL roles. Mirror v7/v8_lightning_70b.sh.
export HELPE_MAX_TOKENS_AGENT1_USER_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3A_ATTR_UPDATE=2500
export HELPE_MAX_TOKENS_AGENT3B_TTM_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3C_EDGE_SUMMARY=2500
export HELPE_MAX_TOKENS_AGENTX_ROLLING_SUMMARY=2000
export HELPE_MAX_TOKENS_AGENTQ_RETRIEVAL_QUERY=2000
export HELPE_MAX_TOKENS_MIND1_V6=3000

# CAMI side — uses the OpenAI SDK directly. Point at the same Lightning
# endpoint serving llama-3.3-70b. The OpenAI SDK appends
# /chat/completions to base_url, so OPENAI_BASE_URL needs the /v1
# suffix (HELP-E's client appends /v1 itself, which is why
# HELPE_MAIN_OLLAMA_URL omits it).
export OPENAI_BASE_URL="https://lightning.ai/api/v1"
export OPENAI_API_KEY="${HELPE_MAIN_API_KEY}"

# Vendored CAMI code lives here.
export HELPE_CAMI_ROOT="$REPO/external/CAMI"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_cami_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_cami_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_cami_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

if ! /usr/bin/python3 -c "import openai, backoff, regex" 2>/dev/null; then
    echo "[run_cami_lightning_70b] missing CAMI deps (openai/backoff/regex)" >&2
    echo "[run_cami_lightning_70b]   /usr/bin/python3 -m pip install --user openai backoff regex" >&2
    exit 2
fi

# Preflight on Lightning llama-3.3-70b (HELP-E's LLM client URL form).
echo "[run_cami_lightning_70b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_cami_lightning_70b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_cami_lightning_70b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_cami_lightning_70b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_cami_lightning_70b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b (CAMI tenant).
echo "[run_cami_lightning_70b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_cami_lightning_70b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_cami_lightning_70b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_cami_lightning_70b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_cami_lightning_70b] SIM preflight OK"

echo "[run_cami_lightning_70b] launching cami with: $*"
exec /usr/bin/python3 -m help_e.run --system cami "$@"
