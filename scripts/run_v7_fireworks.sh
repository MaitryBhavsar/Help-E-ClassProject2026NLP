#!/usr/bin/env bash
# V7 on Fireworks AI — parallel-safe sibling of run_v7_local_120b.sh.
# All roles route to Fireworks (gpt-oss-120b for MAIN/JUDGE; gpt-oss-20b
# for SIM/SMALL); leaves the local 11436 vLLM and Lightning AI account
# free for a parallel V3 run.
#
# Output: output/fireworks_v7_120b/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ ! -f "$REPO/.env.fireworks" ]; then
    echo "[run_v7_fireworks] missing $REPO/.env.fireworks (HELPE_MAIN_API_KEY=fw_...)" >&2
    exit 2
fi
set -a
# shellcheck disable=SC1091
source "$REPO/.env.fireworks"
set +a

if [ -z "${HELPE_MAIN_API_KEY:-}" ]; then
    echo "[run_v7_fireworks] HELPE_MAIN_API_KEY not set after sourcing .env.fireworks" >&2
    exit 2
fi

# All roles → Fireworks
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

# Same v7 max-token bumps.
export HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7=4000
export HELPE_MAX_TOKENS_AGENT5_RESPONSE_V7=3000
export HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE=3000
export HELPE_MAX_TOKENS_MITI_JUDGE=2000
export HELPE_MAX_TOKENS_ESC_JUDGE=2500

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

export HELPE_TRANSCRIPT_DIR="$REPO/output/fireworks_v7_120b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/fireworks_v7_120b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/fireworks_v7_120b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[run_v7_fireworks] launching v7 on Fireworks with: $*"
exec /usr/bin/python3 -m help_e.run --system v7 "$@"
