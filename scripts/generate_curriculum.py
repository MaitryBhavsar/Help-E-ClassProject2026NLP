"""Generate the pre-experiment curriculum for the v1/v3/v4/v6 ablation.

Runs ONCE before any matrix experiments. For each profile:
  B1. one LLM call → eligible_problems (filter the 20-vocab to what's
      plausible for this person)
  B2. (no LLM) deterministic scenario assignment + per-session seed
      problems with carry-forward governed by archetype
  B3. one LLM call per session → frozen session_context with the seed
      problems baked in

Output (under `data/curricula/`):
  scenario_assignments.json                          # 30 profiles × scenario
  <profile_id>/eligible_problems.json                # one per profile
  <profile_id>/session_context_s01.json … s04.json   # one per session

LLM cost: 30 (B1) + 30 × 4 (B3) = 150 calls total. All routed to the
JUDGE endpoint (llama-3.3-70b on local vLLM) for quality + zero
contention with chatbot calls.

Usage:
    PYTHONPATH=src python scripts/generate_curriculum.py
    PYTHONPATH=src python scripts/generate_curriculum.py --profiles P02 P10
    PYTHONPATH=src python scripts/generate_curriculum.py --max-parallel 4
    PYTHONPATH=src python scripts/generate_curriculum.py --force         # overwrite existing
    PYTHONPATH=src python scripts/generate_curriculum.py --dry-run       # don't call LLM, just plan
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from help_e import config
from help_e.curriculum import (
    SCENARIO_TYPES,
    assign_scenario_types,
    generate_curriculum_for_profile,
    pick_seed_problems,
    write_scenario_assignments,
    _profile_spec_to_simulator_profile,
)
from help_e.llm_client import LLMClient
from help_e.profile_spec import list_profiles, load_profile


log = logging.getLogger(__name__)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="scripts/generate_curriculum")
    p.add_argument("--profiles", nargs="*", default=None,
                   help="profile ids to generate (default: all on disk)")
    p.add_argument("--sessions", type=int, default=4,
                   help="sessions per profile (default 4)")
    p.add_argument("--max-parallel", type=int, default=4,
                   help="how many profiles to process concurrently (default 4)")
    p.add_argument("--force", action="store_true",
                   help="overwrite existing curriculum files")
    p.add_argument("--dry-run", action="store_true",
                   help="don't call the LLM; just print what WOULD be generated")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Scenario assignment is ALWAYS based on the full profile set on
    # disk so individual --profiles reruns get the same scenario as
    # the full sweep (deterministic stratification).
    all_profile_ids = list_profiles()
    if not all_profile_ids:
        log.error("no profiles found under %s", config.PROFILE_DIR)
        return 1

    profile_ids = list(args.profiles) if args.profiles else all_profile_ids
    unknown = [pid for pid in profile_ids if pid not in all_profile_ids]
    if unknown:
        log.error("unknown profile ids: %s", unknown)
        return 1

    # --- Scenario assignment (no LLM, on FULL set) ---
    assignments = write_scenario_assignments(all_profile_ids)
    log.info("scenario distribution (full set): %s",
             {s: sum(1 for v in assignments.values() if v == s)
              for s in SCENARIO_TYPES})

    if args.dry_run:
        # Show what seeds would be picked per (profile, session) without
        # actually calling the LLM. Uses an empty eligible set per
        # profile (full vocab) since B1 hasn't run.
        log.info("DRY-RUN — no LLM calls; printing scenario + seeds plan")
        for pid in profile_ids[:5]:
            scenario = assignments[pid]
            log.info("  %s → %s", pid, scenario)
        return 0

    client = LLMClient()
    t0 = time.monotonic()
    results: list[dict] = []
    errors: list[tuple[str, Exception]] = []

    def _do_profile(pid: str) -> dict:
        spec = load_profile(pid)
        scenario = assignments[pid]
        return generate_curriculum_for_profile(
            client=client, profile_spec=spec, scenario=scenario,
            n_sessions=args.sessions,
            skip_existing=not args.force,
        )

    parallel = max(1, min(args.max_parallel, len(profile_ids)))
    log.info(
        "starting curriculum gen: profiles=%d sessions/profile=%d parallel=%d "
        "(LLM calls = %d B1 + %d B3 = %d total)",
        len(profile_ids), args.sessions, parallel,
        len(profile_ids), len(profile_ids) * args.sessions,
        len(profile_ids) * (1 + args.sessions),
    )

    if parallel == 1:
        for pid in profile_ids:
            try:
                res = _do_profile(pid)
                results.append(res)
                log.info("done: %s (%s)", pid, res["scenario"])
            except Exception as e:
                log.exception("FAIL: %s: %s", pid, e)
                errors.append((pid, e))
    else:
        with ThreadPoolExecutor(max_workers=parallel) as ex:
            futures = {ex.submit(_do_profile, pid): pid for pid in profile_ids}
            for fut in as_completed(futures):
                pid = futures[fut]
                try:
                    res = fut.result()
                    results.append(res)
                    log.info("done: %s (%s)", pid, res["scenario"])
                except Exception as e:
                    log.exception("FAIL: %s: %s", pid, e)
                    errors.append((pid, e))

    elapsed = time.monotonic() - t0
    log.info(
        "curriculum gen finished: %d ok, %d failed in %.0fs",
        len(results), len(errors), elapsed,
    )
    if errors:
        for pid, e in errors:
            log.error("  FAIL %s: %s", pid, e)
        return 2
    # Quick summary by scenario for human eyeballing.
    by_scen: dict[str, list[str]] = {s: [] for s in SCENARIO_TYPES}
    for r in results:
        by_scen[r["scenario"]].append(r["profile_id"])
    for s in SCENARIO_TYPES:
        log.info("  %-7s n=%2d profiles=%s", s, len(by_scen[s]),
                 ", ".join(by_scen[s][:5]) + ("..." if len(by_scen[s]) > 5 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
