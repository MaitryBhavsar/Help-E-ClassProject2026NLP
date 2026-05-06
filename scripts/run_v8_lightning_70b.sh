#!/usr/bin/env bash
# HELP-E v8 (RAG variant) routed entirely to Lightning AI:
#   MAIN  + JUDGE → lightning-ai/llama-3.3-70b
#   SIM   + SMALL → lightning-ai/gpt-oss-20b
# Output: output/lightning_v8_70b/
#
# v8 also requires sentence-transformers locally for the MiniLM dense
# retriever (see run_v8_local_120b.sh for the install hint).
#
# Usage:
#   scripts/run_v8_lightning_70b.sh --profile P18 --sessions 3 --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v8_lightning_70b] missing $REPO/.env.local (HELPE_SIM_API_KEY)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v8_lightning_70b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# MAIN → Lightning AI llama-3.3-70b.
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

# JUDGE → same.
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

# SIM → Lightning gpt-oss-20b on the maitry.bhavsar35@gmail.com tenant.
# Hard-coded per the user's request to split SIM/SMALL load across two
# Lightning tenants (v7 → tenant A, v8 → tenant B) to halve the 429
# pressure on each gpt-oss-20b endpoint.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing.
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-70b: clear gpt-oss-specific token bumps and reasoning_effort.
unset HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7
unset HELPE_MAX_TOKENS_AGENT5_RESPONSE_V8
unset HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE
unset HELPE_MAX_TOKENS_MITI_JUDGE
unset HELPE_MAX_TOKENS_ESC_JUDGE
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
export HELPE_MAX_TOKENS_AGENTQ_RETRIEVAL_QUERY=2000
export HELPE_MAX_TOKENS_MIND1_V6=3000

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_v8_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_v8_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_v8_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# sentence-transformers sanity check (v8 dense retriever).
PYBIN="$(command -v python || command -v python3)"
if ! "$PYBIN" -c "import sentence_transformers" 2>/dev/null; then
    echo "[run_v8_lightning_70b] sentence-transformers is not installed for $PYBIN" >&2
    echo "[run_v8_lightning_70b] run: $PYBIN -m pip install --user sentence-transformers" >&2
    exit 2
fi

# Preflight on Lightning llama-3.3-70b.
echo "[run_v8_lightning_70b] preflight (chat ping on $HELPE_MAIN_MODEL @ $HELPE_MAIN_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v8_lightning_70b_main.json -w "%{http_code}" \
    -X POST "${HELPE_MAIN_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_MAIN_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_MAIN_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v8_lightning_70b] preflight FAILED on MAIN: HTTP $http_code" >&2
    cat /tmp/run_v8_lightning_70b_main.json >&2
    echo >&2
    exit 1
fi
echo "[run_v8_lightning_70b] MAIN preflight OK"

# Preflight on Lightning gpt-oss-20b.
echo "[run_v8_lightning_70b] preflight (chat ping on $HELPE_SIM_MODEL @ $HELPE_SIM_OLLAMA_URL)..."
http_code=$(curl -sS -o /tmp/run_v8_lightning_70b_sim.json -w "%{http_code}" \
    -X POST "${HELPE_SIM_OLLAMA_URL}/v1/chat/completions" \
    -H "Authorization: Bearer ${HELPE_SIM_API_KEY}" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"${HELPE_SIM_MODEL}\",\"messages\":[{\"role\":\"user\",\"content\":\"reply with one word: ping\"}],\"max_tokens\":50,\"temperature\":0}")
if [ "$http_code" != "200" ]; then
    echo "[run_v8_lightning_70b] preflight FAILED on SIM: HTTP $http_code" >&2
    cat /tmp/run_v8_lightning_70b_sim.json >&2
    echo >&2
    exit 1
fi
echo "[run_v8_lightning_70b] SIM preflight OK"

echo "[run_v8_lightning_70b] launching v8 with: $*"
exec "$PYBIN" -m help_e.run --system v8 "$@"
