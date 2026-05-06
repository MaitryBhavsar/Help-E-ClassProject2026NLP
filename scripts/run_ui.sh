#!/usr/bin/env bash
# Start the HELP-E demo UI with repo-relative PYTHONPATH and the same
# Lightning AI routing as scripts/run_v8_lightning_70b.sh, so the UI's
# v3/v1/v7/v8 systems can hit MAIN (llama-3.3-70b) and SIM (gpt-oss-20b)
# without 401s.
# Usage: ./scripts/run_ui.sh
# Optional: HELPE_UI_PORT (default 8765), PYTHON (default python3).

set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -f .env.local ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.local
  set +a
fi

# MAIN -> Lightning llama-3.3-70b. The fallback key matches what
# scripts/run_v7_lightning_70b.sh and run_v8_lightning_70b.sh use.
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

# JUDGE -> same Lightning llama-3.3-70b tenant as MAIN.
export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_JUDGE_API_KEY:-$HELPE_MAIN_API_KEY}"

# SIM -> Lightning gpt-oss-20b. Pulled from .env.local; falls back to
# the v8 tenant key if .env.local is missing.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

# SMALL routing matches SIM (same model, same tenant).
export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"

# llama-70b token-budget tuning (mirror of run_v8_lightning_70b.sh).
unset HELPE_MAX_TOKENS_AGENT2_INFERENCE_V7
unset HELPE_MAX_TOKENS_AGENT5_RESPONSE_V8
unset HELPE_MAX_TOKENS_AGENT_P_PERSONA_UPDATE
unset HELPE_MAX_TOKENS_MITI_JUDGE
unset HELPE_MAX_TOKENS_ESC_JUDGE
unset HELPE_REASONING_EFFORT
export HELPE_MAX_TOKENS_AGENT1_USER_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3A_ATTR_UPDATE=2500
export HELPE_MAX_TOKENS_AGENT3B_TTM_INTENT=2000
export HELPE_MAX_TOKENS_AGENT3C_EDGE_SUMMARY=2500
export HELPE_MAX_TOKENS_AGENTX_ROLLING_SUMMARY=2000
export HELPE_MAX_TOKENS_AGENTQ_RETRIEVAL_QUERY=2000
export HELPE_MAX_TOKENS_MIND1_V6=3000

export PYTHONPATH="${ROOT}/src${PYTHONPATH:+:$PYTHONPATH}"
PORT="${HELPE_UI_PORT:-8765}"
PY="${PYTHON:-python3}"

exec "$PY" -m help_e.ui.server --host 127.0.0.1 --port "$PORT"
