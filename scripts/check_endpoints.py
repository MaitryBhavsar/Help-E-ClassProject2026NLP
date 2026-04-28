"""Verify all three model endpoints are reachable + serving the
expected models BEFORE launching a matrix run.

Endpoints (production setup):
  - MAIN  → gpt-oss-120b on Lightning AI (account A)
  - SIM   → gpt-oss-20b  on Lightning AI (account B, different from MAIN)
  - JUDGE → llama-3.3-70b on local vLLM (no API key)

Usage:
    PYTHONPATH=src python scripts/check_endpoints.py

Exits 0 on full success, 1 if any endpoint is down or model missing.

Required env vars (configured in your shell before running matrix):
    export HELPE_MAIN_API_KEY=<lightning-ai key for gpt-oss-120b account>
    export HELPE_SIM_API_KEY=<lightning-ai key for gpt-oss-20b account>
    export HELPE_JUDGE_API_KEY=EMPTY            # local vLLM
    export HELPE_JUDGE_OLLAMA_URL=http://localhost:11436   # if not 11436
    export HELPE_JUDGE_MODEL=meta-llama/Llama-3.3-70B-Instruct
"""
from __future__ import annotations

import sys
from typing import Optional

import requests

from help_e import config


def _list_models(url: str, key: str, timeout: int = 5) -> Optional[list[str]]:
    try:
        r = requests.get(
            f"{url}/v1/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=timeout,
        )
        if not r.ok:
            return None
        body = r.json()
        return [m.get("id") for m in (body.get("data") or []) if m.get("id")]
    except requests.RequestException:
        return None


def _check_one(name: str, url: str, expected_model: str, key: str) -> tuple[bool, str]:
    models = _list_models(url, key)
    if models is None:
        return False, f"{name:6s} {url:50s} UNREACHABLE (network/auth fail)"
    if expected_model not in models:
        # Some servers list models with a slightly different prefix; accept
        # a substring match as well.
        if not any(expected_model in m or m in expected_model for m in models):
            return False, (
                f"{name:6s} {url:50s} reachable but '{expected_model}' "
                f"not in served models {models[:5]}"
            )
    return True, f"{name:6s} {url:50s} OK ({expected_model})"


def main() -> int:
    print("Endpoint health check")
    print("=" * 78)
    rows = [
        ("MAIN",  config.MAIN_OLLAMA_URL,  config.MAIN_MODEL_NAME,  config.MAIN_API_KEY),
        ("SIM",   config.SIM_OLLAMA_URL,   config.SIM_MODEL_NAME,   config.SIM_API_KEY),
        ("JUDGE", config.JUDGE_OLLAMA_URL, config.JUDGE_MODEL_NAME, config.JUDGE_API_KEY),
    ]
    all_ok = True
    for name, url, model, key in rows:
        if not key or key == "EMPTY":
            # Local vLLM accepts any string; warn if MAIN/SIM look unset.
            if name in ("MAIN", "SIM"):
                print(f"{name:6s} {url:50s} ❌ HELPE_{name}_API_KEY not set")
                all_ok = False
                continue
        ok, msg = _check_one(name, url, model, key)
        print(("✓ " if ok else "✗ ") + msg)
        if not ok:
            all_ok = False
    print("=" * 78)
    if all_ok:
        print("All 3 endpoints OK. Safe to launch matrix.")
        return 0
    print("One or more endpoints failed. Fix before launching matrix.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
