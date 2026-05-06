#!/usr/bin/env bash
# V1 (history-only baseline) on Lightning AI:
#   MAIN  + JUDGE → lightning-ai/llama-3.3-70b
#   SIM   + SMALL → lightning-ai/gpt-oss-20b
# (Same Lightning account/key as V7 — ``HELPE_SIM_API_KEY`` from
# ``.env.local`` works for both models on Lightning.)
#
# Output: output/lightning_v1_70b/

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# V1 routing on Lightning with SPLIT KEYS (one per model) so we don't hit
# per-key rate limits when V3 is also running:
#   MAIN + JUDGE (llama-3.3-70b)  → key 7eaac001-... (shared with V3 main)
#   SIM  + SMALL (gpt-oss-20b)    → key 9c9c9b98-...  (dweeptrivedi account,
#                                   distinct from V3's 780abb... gpt-oss key)
# Keys are pinned in this script so .env.local (used by V3) stays untouched.

# MAIN + JUDGE → Lightning AI llama-3.3-70b (large model, BIG calls).
export HELPE_MAIN_OLLAMA_URL="https://lightning.ai/api"
export HELPE_MAIN_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_MAIN_API_KEY="${HELPE_MAIN_API_KEY:?set HELPE_MAIN_API_KEY in .env.local}"

export HELPE_JUDGE_OLLAMA_URL="https://lightning.ai/api"
export HELPE_JUDGE_MODEL="lightning-ai/llama-3.3-70b"
export HELPE_JUDGE_API_KEY="${HELPE_JUDGE_API_KEY:?set HELPE_JUDGE_API_KEY in .env.local}"

# SIM + SMALL → Lightning AI gpt-oss-20b (small calls), dweeptrivedi key.
export HELPE_SIM_OLLAMA_URL="https://lightning.ai/api"
export HELPE_SIM_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SIM_API_KEY="${HELPE_SIM_API_KEY:?set HELPE_SIM_API_KEY in .env.local}"

export HELPE_SMALL_URL="https://lightning.ai/api"
export HELPE_SMALL_MODEL="lightning-ai/gpt-oss-20b"
export HELPE_SMALL_API_KEY="${HELPE_SMALL_API_KEY:?set HELPE_SMALL_API_KEY in .env.local}"

export HELPE_REASONING_EFFORT="${HELPE_REASONING_EFFORT:-low}"

# Isolated output tree shared across all V1-Lightning profile runs.
export HELPE_TRANSCRIPT_DIR="$REPO/output/lightning_v1_70b/transcripts"
export HELPE_GRAPH_V6_DIR="$REPO/output/lightning_v1_70b/graphs_v6"
export HELPE_LOG_ROOT="$REPO/output/lightning_v1_70b/logs"

mkdir -p "$HELPE_TRANSCRIPT_DIR" "$HELPE_GRAPH_V6_DIR" "$HELPE_LOG_ROOT"

cd "$REPO"
export PYTHONPATH="src:${PYTHONPATH:-}"

echo "[run_v1_lightning_70b] launching v1 with: $*"
exec /usr/bin/python3 -m help_e.run --system v1 "$@"
