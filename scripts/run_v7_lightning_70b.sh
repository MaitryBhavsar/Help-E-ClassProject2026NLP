#!/usr/bin/env bash
# HELP-E v7 routed entirely to Lightning AI:
#   MAIN  (Agent 2 inference, Agent 5 response, Agent P persona)
#       → lightning-ai/llama-3.3-70b   (key: HELPE_MAIN_API_KEY)
#   JUDGE (miti_judge, esc_judge)
#       → lightning-ai/llama-3.3-70b   (same key)
#   SIM   (mind1_v6, session_context, simulator)
#       → lightning-ai/gpt-oss-20b     (key: HELPE_SIM_API_KEY from .env.local)
#   SMALL (Agent 1, Agent X, Agent 3a, Agent 3b)
#       → lightning-ai/gpt-oss-20b     (inherits SIM)
#
# Output: output/lightning_v7_70b/
#
# Usage:
#   scripts/run_v7_lightning_70b.sh --profile P18 --sessions 3 --turns-list 30,20,20
#   scripts/run_v7_lightning_70b.sh --profile P01 --sessions 1 --turns 5

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# .env.local supplies HELPE_SIM_API_KEY (gpt-oss-20b tenant). The MAIN
# key is hard-coded below per the user's request to use this specific
# Lightning AI key for llama-3.3-70b traffic.
if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v7_lightning_70b] missing $REPO/.env.local (HELPE_SIM_API_KEY)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v7_lightning_70b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN → Lightning AI llama-3.3-70b (large LLM calls).
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

# JUDGE → same Lightning llama-3.3-70b (same tenant).
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

# SIM → Lightning gpt-oss-20b on the dweeptrivedi2015@gmail.com tenant.
# Hard-coded per the user's request to split SIM/SMALL load across two
# Lightning tenants (v7 → tenant A, v8 → tenant B) to halve the 429
# pressure on each gpt-oss-20b endpoint.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing — same Lightning gpt-oss-20b tenant as SIM.
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-3.3-70b does NOT emit reasoning_content separately, so the
# v7 default token caps (already tuned in config.py) are fine. Override
# only if smoke shows truncation.
# (Previously gpt-oss-120b runs bumped these to 4000/3000 because of
#  reasoning_content overhead; on llama-70b that overhead doesn't exist.)
unset HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7
unset HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7
unset HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE
unset HELPE_MAX_TOKENS_MITI_JUDGE
unset HELPE_MAX_TOKENS_ESC_JUDGE

# Reasoning-effort knob is gpt-oss only; clearing it for llama traffic.
unset HELPE_REASONING_EFFORT

# All gpt-oss-20b small-model roles suffer from reasoning_content eating
# the token budget before the assistant content emits, producing empty
# responses + JSON-parse retry storms. Defaults are sized for non-CoT
# models, so we bump every small role generously. This near-eliminates
# JSON_parse retries on small-model traffic. Mild over-allocation is
# fine: max_tokens is a CEILING, not a target.
export HELPE_MAX_TOKENS_AGENT1_USER_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3A_ATTR_UPDATE=2500
export HELPE_MAX_TOKENS_AGENT3B_TTM_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3C_EDGE_SUMMARY=2500
export HELPE_MAX_TOKENS_AGENTX_ROLLING_SUMMARY=2000
export HELPE_MAX_TOKENS_AGENTQ_RETRIEVAL_QUERY=2000   # v8-only role
export HELPE_MAX_TOKENS_MIND1_V6=3000                  # nudge from 2500

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_v7_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_v7_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_v7_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Preflight on Lightning llama-3.3-70b.
echo "[run_v7_lightning_70b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_lightning_70b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_lightning_70b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_v7_lightning_70b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_lightning_70b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b.
echo "[run_v7_lightning_70b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v7_lightning_70b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v7_lightning_70b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_v7_lightning_70b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_v7_lightning_70b] SIM preflight OK"

echo "[run_v7_lightning_70b] launching v7 with: $*"
exec python -m help_e.run --system v7 "$@"
