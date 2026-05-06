#!/usr/bin/env bash
# CAMI baseline on Fireworks AI — parallel-safe sibling of
# run_cami_local_120b.sh. All LLM roles route to Fireworks
# (gpt-oss-120b for HELP-E's MAIN/JUDGE; gpt-oss-20b for SIM/SMALL).
#
# CAMI uses the OpenAI Python SDK directly, so we ALSO export
# OPENAI_BASE_URL + OPENAI_API_KEY pointing at the same Fireworks
# endpoint that HELP-E's MAIN endpoint uses.
#
# Output: output/fireworks_cami_120b/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.fireworks" ]; then
    echo "[run_cami_fireworks] missing $REPO/.env.fireworks" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.fireworks"
set +a

if [ -z "${HELPE_MAIN_API_KEY:-}" ]; then
    echo "[run_cami_fireworks] HELPE_MAIN_API_KEY not set after sourcing .env.fireworks" >&2
    exit 2
fi

# HELP-E side — all roles → Fireworks.
export HELPE_MAIN_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_MAIN_MODEL="accounts/fireworks/models/gpt-oss-120b"

export HELPE_JUDGE_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_JUDGE_MODEL="accounts/fireworks/models/gpt-oss-120b"
export HELPE_JUDGE_API_KEY="${HELPE_MAIN_API_KEY}"

export HELPE_SIM_OLLAMA_URL="https://api.fireworks.ai/inference"
export HELPE_SIM_MODEL="accounts/fireworks/models/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_MAIN_API_KEY}"

export HELPE_SMALL_URL="https://api.fireworks.ai/inference"
export HELPE_SMALL_MODEL="accounts/fireworks/models/gpt-oss-20b"

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# CAMI side — uses the OpenAI SDK directly. Point it at the same
# Fireworks endpoint serving gpt-oss-120b.
export OPENAI_BASE_URL="https://api.fireworks.ai/inference/v1"
export OPENAI_API_KEY="${HELPE_MAIN_API_KEY}"

# Vendored CAMI code lives here.
export HELPE_CAMI_ROOT="$REPO/external/CAMI"

# Isolated output tree.
export HELPE_TRANSCRIPT_DIR="$REPO/output/fireworks_cami_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/fireworks_cami_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/fireworks_cami_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

if ! /usr/bin/python3 -c "import openai, backoff, regex" 2>/dev/null; then
    echo "[run_cami_fireworks] missing CAMI deps (openai/backoff/regex)" >&2
    exit 2
fi

echo "[run_cami_fireworks] launching cami on Fireworks with: $*"
exec /usr/bin/python3 -m help_e.run --system cami "$@"
