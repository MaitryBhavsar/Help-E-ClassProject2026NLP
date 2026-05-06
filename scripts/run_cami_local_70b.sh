#!/usr/bin/env bash
# CAMI baseline (vendored under external/CAMI) routed to match
# run_v7_lightning_70b.sh / run_v8_lightning_70b.sh on the COUNSELOR
# model side, but on local hardware:
#   MAIN  + JUDGE → local vLLM @ http://localhost:11436
#                   serving meta-llama/Llama-3.3-70B-Instruct
#   SIM   + SMALL → Lightning AI gpt-oss-20b on dweeptrivedi2015 tenant
#                   (matches v7's SIM tenant — clean head-to-head)
# Output: output/local_cami_11436_70b/
#
# CAMI uses the OpenAI Python SDK directly (separate from HELP-E's LLM
# client), so we ALSO export OPENAI_API_KEY + OPENAI_BASE_URL pointing
# at the same local vLLM that HELP-E's MAIN endpoint uses.
#
# Usage:
#   scripts/run_cami_local_70b.sh --profile P01 --sessions 1 --turns 5
#   scripts/run_cami_local_70b.sh --profile P01 --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_cami_local_70b] missing $REPO/.env.local" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

# HELP-E side — MAIN + JUDGE on local vLLM (llama-3.3-70b).
export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="meta-llama/Llama-3.3-70B-Instruct"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="meta-llama/Llama-3.3-70B-Instruct"
export HELPE_JUDGE_API_KEY="EMPTY"

# SIM → Lightning gpt-oss-20b on a dedicated CAMI tenant (key set per
# user directive 2026-05-05). Splits load away from the v7 tenant
# (9c9c9b98...) and v8 tenant (780abb93...), so concurrent CAMI runs
# do not steal rate-limit budget from in-flight v7/v8 work. The
# simulator model class still matches v7/v8 (gpt-oss-20b) — what
# governs comparability is the model, not the tenant.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing — same Lightning gpt-oss-20b tenant as SIM. (CAMI does
# not use HELP-E's SMALL roles, but unset/wrong values can leak into
# HELP-E session-context generation, so set them explicitly.)
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-3.3-70b is a non-CoT model: no reasoning_content overhead, so
# the v7_lightning_70b.sh-style token bumps are unnecessary here. Keep
# only the small-model bumps (gpt-oss-20b on Lightning DOES emit
# reasoning_content and benefits from larger ceilings).
unset HELPE_REASONING_EFFORT
unset HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7
unset HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7
unset HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE
unset HELPE_MAX_TOKENS_MITI_JUDGE
unset HELPE_MAX_TOKENS_ESC_JUDGE

export HELPE_MAX_TOKENS_AGENT1_USER_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3A_ATTR_UPDATE=2500
export HELPE_MAX_TOKENS_AGENT3B_TTM_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3C_EDGE_SUMMARY=2500
export HELPE_MAX_TOKENS_AGENTX_ROLLING_SUMMARY=2000
export HELPE_MAX_TOKENS_AGENTQ_RETRIEVAL_QUERY=2000
export HELPE_MAX_TOKENS_MIND1_V6=3000

# CAMI side — uses the OpenAI SDK directly. Point at the same local
# vLLM serving llama-3.3-70b.
export OPENAI_BASE_URL="http://localhost:11436/v1"
export OPENAI_API_KEY="EMPTY"

# Vendored CAMI code lives here.
export HELPE_CAMI_ROOT="$REPO/external/CAMI"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_cami_11436_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_cami_11436_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_cami_11436_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

if ! /usr/bin/python3 -c "import openai, backoff, regex" 2>/dev/null; then
    echo "[run_cami_local_70b] missing CAMI deps (openai/backoff/regex)" >&2
    echo "[run_cami_local_70b]   /usr/bin/python3 -m pip install --user openai backoff regex" >&2
    exit 2
fi

# Preflight on local vLLM.
echo "[run_cami_local_70b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_cami_local_70b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_cami_local_70b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_cami_local_70b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_cami_local_70b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b (dweep tenant).
echo "[run_cami_local_70b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_cami_local_70b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_cami_local_70b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_cami_local_70b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_cami_local_70b] SIM preflight OK"

echo "[run_cami_local_70b] launching cami with: $*"
exec /usr/bin/python3 -m help_e.run --system cami "$@"
