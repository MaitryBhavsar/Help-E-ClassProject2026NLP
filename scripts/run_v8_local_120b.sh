#!/usr/bin/env bash
# V8 (RAG with MiniLM + MMR) using the same routing as v7_local_120b:
#   MAIN  + JUDGE → local vLLM @ http://localhost:11436 (gpt-oss-120b)
#   SIM   + SMALL → Lightning AI gpt-oss-20b
# Output: output/local_v8_11436_120b/
#
# V8 also requires sentence-transformers installed locally for the
# MiniLM dense retriever.
#
# Usage:
#   scripts/run_v8_local_120b.sh --profile P01 --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_v8_local_120b] missing $REPO/.env.local" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_v8_local_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
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

export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# V7 max-token bumps apply to V8 too (V8 reuses v7 schemas).
export HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7=4000
export HELPE_MAX_TOKENS_AGENT5_RESPONSE_V8=3000
export HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE=3000
export HELPE_MAX_TOKENS_MITI_JUDGE=2000
export HELPE_MAX_TOKENS_ESC_JUDGE=2500

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_v8_11436_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_v8_11436_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_v8_11436_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Sanity-check sentence-transformers is reachable.
if ! /usr/bin/python3 -c "import sentence_transformers" 2>/dev/null; then
    echo "[run_v8_local_120b] sentence-transformers is not installed for /usr/bin/python3" >&2
    echo "[run_v8_local_120b] run: /usr/bin/python3 -m pip install --user sentence-transformers" >&2
    exit 2
fi

echo "[run_v8_local_120b] launching v8 with: $*"
exec /usr/bin/python3 -m help_e.run --system v8 "$@"
