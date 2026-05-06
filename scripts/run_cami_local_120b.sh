#!/usr/bin/env bash
# CAMI baseline (vendored under external/CAMI) using the same routing
# config as run_v7_local_120b.sh:
#   MAIN  + JUDGE → local vLLM @ http://localhost:11436 (gpt-oss-120b)
#   SIM   + SMALL → Lightning AI gpt-oss-20b
# Output: output/local_cami_11436_120b/
#
# CAMI uses the OpenAI Python SDK directly (separate from HELP-E's LLM
# client), so we ALSO export OPENAI_API_KEY + OPENAI_BASE_URL pointing
# at the same local vLLM that HELP-E's MAIN endpoint uses.
#
# Usage:
#   scripts/run_cami_local_120b.sh --profile P18 --turns-list 30,20,20

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.local" ]; then
    echo "[run_cami_local_120b] missing $REPO/.env.local" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.local"
set +a

if [ -z "${HELPE_SIM_API_KEY:-}" ]; then
    echo "[run_cami_local_120b] HELPE_SIM_API_KEY not set after sourcing .env.local" >&2
    exit 2
fi

# HELP-E side — same routing as run_v7_local_120b.sh.
export HELPE_MAIN_OLLAMA_URL="http://localhost:11436"
export HELPE_MAIN_MODEL="openai/gpt-oss-120b"
export HELPE_MAIN_API_KEY="EMPTY"

export HELPE_JUDGE_OLLAMA_URL="http://localhost:11436"
export HELPE_JUDGE_MODEL="openai/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="EMPTY"

export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"

export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# CAMI side — uses the OpenAI SDK directly, separate from HELP-E's
# LLM client. Point it at the same local vLLM serving gpt-oss-120b.
export OPENAI_BASE_URL="http://localhost:11436/v1"
export OPENAI_API_KEY="EMPTY"

# Where CAMI's vendored code lives (resolved by cami_adapter._resolve_cami_root).
export HELPE_CAMI_ROOT="$REPO/external/CAMI"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/local_cami_11436_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/local_cami_11436_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/local_cami_11436_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

# Sanity-check the CAMI Python deps.
if ! /usr/bin/python3 -c "import openai, backoff, regex" 2>/dev/null; then
    echo "[run_cami_local_120b] missing CAMI deps (openai/backoff/regex)" >&2
    echo "[run_cami_local_120b]   /usr/bin/python3 -m pip install --user openai backoff regex" >&2
    exit 2
fi

echo "[run_cami_local_120b] launching cami with: $*"
exec /usr/bin/python3 -m help_e.run --system cami "$@"
