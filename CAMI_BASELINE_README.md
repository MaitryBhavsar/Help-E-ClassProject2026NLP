# Running CAMI as a Baseline Counselor

This guide explains how to generate baseline comparison results with CAMI as
the counselor policy. CAMI is treated as an external baseline and compared
against HELP-E systems such as `v1`, `v3`, and `v6`.

CAMI is integrated through:

- `external/CAMI/`: vendored CAMI implementation
- `src/help_e/baselines/cami_adapter.py`: HELP-E adapter that exposes CAMI as
  `cami_turn_fn`
- `src/help_e/run.py`: CLI system selector, intended to accept
  `--system cami`
- `src/help_e/session_driver_v6.py`: shared driver used for comparable
  transcripts, simulator turns, graph snapshots, and judges

In this setup, CAMI only replaces the counselor response policy. HELP-E still
owns the simulated client, session loop, transcript persistence, graph snapshot
layout, MITI judge, ESC judge, and result aggregation.

## Quick Start

From the repository root:

```powershell
.\.venv\Scripts\Activate.ps1
$env:PYTHONPATH = "$PWD\src;$env:PYTHONPATH"

python -m help_e.run --system cami --profile P01 --sessions 1 --turns 3 --max-parallel-profiles 1
python -m help_e.eval.matrix_report --system cami
```

For a full paired comparison:

```powershell
foreach ($sys in @("v1", "v3", "v6", "cami")) {
    python -m help_e.run --system $sys --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 4
}

python -m help_e.eval.ablation_report --systems v1 v3 v6 cami --baseline v6
```

## Preflight Checks

Verify that the CLI exposes CAMI:

```powershell
python -m help_e.run --help
```

You should see:

```text
--system {v1,v3,v6,cami}
```

Then compile the files used by the CAMI batch path:

```powershell
python -m py_compile `
  src/help_e/run.py `
  src/help_e/profile_spec.py `
  src/help_e/session_driver_v6.py `
  src/help_e/baselines/cami_adapter.py
```

Note: `src/help_e/session_driver.py` is a legacy/UI shim and is not required by
the CAMI batch run path.

## 1. Install Dependencies

From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install openai backoff regex
```

CAMI uses the OpenAI Python SDK directly, so `openai`, `backoff`, and `regex`
are required in addition to the main HELP-E dependencies.

Set `PYTHONPATH` so `help_e` imports from `src/`:

```powershell
$env:PYTHONPATH = "$PWD\src;$env:PYTHONPATH"
```

For bash:

```bash
export PYTHONPATH="$PWD/src:$PYTHONPATH"
```

## 2. Configure LLM Endpoints

HELP-E and CAMI use different environment variable names.

HELP-E uses `HELPE_*` variables for the simulator and judges. CAMI uses the
OpenAI SDK variables `OPENAI_API_KEY` and `OPENAI_BASE_URL`.

For a local OpenAI-compatible vLLM server:

```powershell
$env:HELPE_MAIN_OLLAMA_URL = "http://127.0.0.1:11436"
$env:HELPE_MAIN_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
$env:HELPE_SIM_OLLAMA_URL = "http://127.0.0.1:11438"
$env:HELPE_SIM_MODEL = "openai/gpt-oss-20b"
$env:HELPE_JUDGE_OLLAMA_URL = "http://127.0.0.1:11436"
$env:HELPE_JUDGE_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
$env:HELPE_VLLM_API_KEY = "EMPTY"

$env:OPENAI_BASE_URL = "http://127.0.0.1:11436/v1"
$env:OPENAI_API_KEY = "EMPTY"
```

For LightningAI or another hosted OpenAI-compatible endpoint:

```powershell
$env:HELPE_MAIN_OLLAMA_URL = "https://lightning.ai/api"
$env:HELPE_MAIN_MODEL = "lightning-ai/llama-3.3-70b"
$env:HELPE_SIM_OLLAMA_URL = "https://lightning.ai/api"
$env:HELPE_SIM_MODEL = "lightning-ai/gpt-oss-20b"
$env:HELPE_JUDGE_OLLAMA_URL = "https://lightning.ai/api"
$env:HELPE_JUDGE_MODEL = "lightning-ai/llama-3.3-70b"

$env:HELPE_MAIN_API_KEY = "<main-key>"
$env:HELPE_SIM_API_KEY = "<sim-key>"
$env:HELPE_JUDGE_API_KEY = "<judge-key>"

$env:OPENAI_BASE_URL = "https://lightning.ai/api/v1"
$env:OPENAI_API_KEY = "<main-key>"
```

CAMI's adapter chooses its counselor model from:

1. `HELPE_MAIN_MODEL`, if set
2. `src/help_e/config.py::MAIN_MODEL_NAME`
3. fallback: `meta-llama/Llama-3.3-70B-Instruct`

So for a fair comparison, set `HELPE_MAIN_MODEL` to the same model you want
CAMI to use.

Check the HELP-E endpoints:

```powershell
python scripts/check_endpoints.py
```

## 3. Confirm CAMI Can Be Imported

From the repo root:

```powershell
python -c "from help_e.baselines.cami_adapter import cami_turn_fn; print(cami_turn_fn.__name__)"
```

Expected output:

```text
cami_turn_fn
```

If CAMI is stored somewhere else, point the adapter at it:

```powershell
$env:HELPE_CAMI_ROOT = "C:\path\to\CAMI"
```

By default, the adapter expects:

```text
external/CAMI
```

## 4. Run CAMI for One Profile

Small smoke run:

```powershell
python -m help_e.run --system cami --profile P01 --sessions 1 --turns 3 --max-parallel-profiles 1
```

Longer single-profile run:

```powershell
python -m help_e.run --system cami --profile P01 --sessions 4 --turns 10 --max-parallel-profiles 1
```

CAMI keeps an in-memory counselor object per `(profile_id, session_id)`, so a
single session preserves CAMI's internal conversation state across turns.

Generate CAMI-only summary metrics for that profile:

```powershell
python -m help_e.eval.matrix_report --system cami --profiles P01
```

## 5. Generate Baseline Comparison Results

Run the same profile across HELP-E baselines plus CAMI:

```powershell
foreach ($sys in @("v1", "v3", "v6", "cami")) {
    python -m help_e.run --system $sys --profile P01 --sessions 4 --turns 10 --max-parallel-profiles 1
}
```

Then aggregate the paired comparison:

```powershell
python -m help_e.eval.ablation_report --systems v1 v3 v6 cami --baseline v6
```

Run all profiles:

```powershell
foreach ($sys in @("v1", "v3", "v6", "cami")) {
    python -m help_e.run --system $sys --all-profiles --sessions 4 --turns 10 --max-parallel-profiles 4
}
```

Then generate the final table:

```powershell
python -m help_e.eval.ablation_report --systems v1 v3 v6 cami --baseline v6
```

Optional per-system reports:

```powershell
python -m help_e.eval.matrix_report --system v1
python -m help_e.eval.matrix_report --system v3
python -m help_e.eval.matrix_report --system v6
python -m help_e.eval.matrix_report --system cami
```

For an initial debugging pass, keep `--max-parallel-profiles 1`. CAMI makes
multiple OpenAI SDK calls inside one counselor turn, so parallel profile runs
can create heavy request bursts.

## 6. Outputs

After a run, CAMI artifacts are written under the same layout as the v6-aligned
systems:

```text
src/help_e/transcripts/<PROFILE_ID>/cami/
src/help_e/graphs_v6/cami/
src/help_e/logs/<PROFILE_ID>/
```

Useful files:

- `session_01.json`: transcript and per-turn traces
- `mind1_reasoning_s01.jsonl`: simulator sidecar
- `session_context_s01.json`: hidden session framing for the simulator
- `miti_judge_s01.json`: MITI 4.2 judge sidecar
- `esc_judge_s01.json`: ESC judge sidecar
- `run_artifacts.json`: run-level index

CAMI-specific metadata appears inside each turn trace:

```json
{
  "trace": {
    "system": "cami",
    "behavior_given_to_cami": "...",
    "goal_given_to_cami": "...",
    "cami_trace": {
      "inferred_state": "...",
      "topic": "...",
      "strategies": "..."
    }
  }
}
```

CAMI does not run HELP-E inference or recomputation. The adapter marks that
explicitly:

```json
{
  "_cami_no_helpe_inference": true,
  "_cami_no_recompute": true
}
```

## 7. Aggregate Results

CAMI-only results:

```powershell
python -m help_e.eval.matrix_report --system cami
```

Paired baseline comparison across systems:

```powershell
python -m help_e.eval.ablation_report --systems v1 v3 v6 cami --baseline v6
```

JSON output for saving or downstream analysis:

```powershell
python -m help_e.eval.matrix_report --system cami --json > cami_matrix.json
python -m help_e.eval.ablation_report --systems v1 v3 v6 cami --baseline v6 --json > cami_ablation.json
```

The ablation report only includes profiles that have artifacts for all systems
listed in `--systems`, so missing one system for `P07`, for example, drops
`P07` from the paired table.

## 8. Troubleshooting

If `python -m help_e.run --system cami ...` says `invalid choice: cami`, check
that `SYSTEMS` in `src/help_e/run.py` includes `"cami"`.

If imports fail with `No module named agents`, verify:

```powershell
Test-Path external/CAMI/agents/counselor.py
```

or set:

```powershell
$env:HELPE_CAMI_ROOT = "C:\path\to\CAMI"
```

If CAMI calls fail with authentication errors, remember that CAMI uses:

```powershell
$env:OPENAI_API_KEY
$env:OPENAI_BASE_URL
```

while HELP-E uses:

```powershell
$env:HELPE_MAIN_API_KEY
$env:HELPE_SIM_API_KEY
$env:HELPE_JUDGE_API_KEY
```

If hosted endpoint rate limits appear, reduce concurrency:

```powershell
python -m help_e.run --system cami --profile P01 --sessions 1 --turns 3 --max-parallel-profiles 1
```

## 9. What This Comparison Means

The CAMI comparison is not a graph-ablation tier. It is an external counselor
baseline.

For comparability:

- the simulated client is still HELP-E's v6 simulator
- session context generation is still HELP-E's
- MITI and ESC judges are still HELP-E's shared evaluation path
- transcript and sidecar layout matches the v6-aligned systems

The policy being compared is CAMI's STAR-style counselor response generation
versus HELP-E's internal counselor policies.
