"""CLI entrypoint — ``python -m help_e.run`` (§14 Day-4 launcher target).

Runs one or more profiles × systems through ``session_driver.run_profile``
and writes artifacts under ``config.TRANSCRIPT_DIR`` + ``config.GRAPH_DIR``.

Examples:
    python -m help_e.run --system v5 --profile P01 --sessions 2 --turns 6
    python -m help_e.run --system v1 --all-profiles
    python -m help_e.run --system v4 --profile P01 --run-judge

Passes whatever ``HELPE_OLLAMA_URL`` / ``HELPE_MODEL`` environment
variables the shell provides — see ``config.py``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

from . import config
from .llm_client import get_client
from .profile_spec import (
    ProfileSpec, RunConfig, list_profiles, load_profile,
)
from .session_driver_v6 import RunArtifactsV6, run_profile_v6


log = logging.getLogger(__name__)


<<<<<<< HEAD
# Active ablation set: v1 (history-only floor), v3 (per-problem text
# summary + TTM-from-summary), v4 (v3 + free-form cross-problem
# connections), v6 (full HBM attribute graph + HBM-typed connections).
# v2/v5 retired — v3 subsumes v2's per-problem summaries (with TTM on
# top), and v6's response prompt subsumes v5's merged-CoT design.
SYSTEMS: tuple[str, ...] = ("v1", "v3", "v4", "v6")
=======
# Active ablation set after the v6 redesign. v2/v4/v5 were retired —
# v3 cleanly subsumes v2's per-problem summaries (it adds TTM on top),
# and v6 supersedes v4/v5's graph-based pipeline. v1 stays as the
# floor baseline (history-only, ~1 LLM call/turn).
SYSTEMS: tuple[str, ...] = ("v1", "v3", "v6", "cami")
>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)


def _pick_turn_fn_v6_aligned(system: str):
    """v6-aligned dispatcher. ALL systems (v1, v3, v4, v6) now run
    through the v6 driver (`run_profile_v6`) with a per-system
    `turn_fn`. Guarantees the ablation contract: shared simulator,
    shared MISC vocabulary, shared TTM enum, shared 3-field response
    output. v1/v3/v4 share `response_simple`; v6 uses `response_v6`
    (HBM-aware).
    """
    if system == "v1":
        from .baselines.v1_history import v1_turn_fn
        return v1_turn_fn
    if system == "v3":
        from .baselines.v3_ttm_from_summary import v3_turn_fn
        return v3_turn_fn
    if system == "v4":
        from .baselines.v4_obs_ttm import v4_turn_fn
        return v4_turn_fn
    if system == "v6":
        from .baselines.v6_full import v6_turn_fn
        return v6_turn_fn
    if system == "cami":
        from .baselines.cami_adapter import cami_turn_fn
        return cami_turn_fn
    raise ValueError(f"unknown system {system!r}; expected one of {SYSTEMS}")


<<<<<<< HEAD
# Legacy v1–v5 dispatcher removed in the cleanup pass. All active
# systems route through `_pick_turn_fn_v6_aligned`.
=======
# Legacy v1–v5 dispatcher — kept for any external callers that still
# import it. The active matrix uses `_pick_turn_fn_v6_aligned`.
def _pick_turn_fn(system: str) -> TurnFn | None:  # noqa: D401
    if system == "v1":
        from .baselines.v1_history import v1_turn_fn
        return v1_turn_fn
    if system == "v3":
        from .baselines.v3_ttm_from_summary import v3_turn_fn
        return v3_turn_fn
    if system == "v6":
        return None
    if system == "cami":
        from .baselines.cami_adapter import cami_turn_fn
        return cami_turn_fn
    raise ValueError(f"unknown system {system!r}; expected one of {SYSTEMS}")
>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)


def _resolve_profiles(args: argparse.Namespace) -> list[ProfileSpec]:
    if args.all_profiles:
        ids = list_profiles()
    elif args.profile:
        ids = list(args.profile)
    else:
        raise SystemExit("pass --profile ID (repeatable) or --all-profiles")
    if not ids:
        raise SystemExit(
            f"no profiles found under {config.PROFILE_DIR}. "
            "Run the seeding script first (task #31)."
        )
    return [load_profile(pid) for pid in ids]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="help_e.run")
    p.add_argument("--system", required=True, choices=SYSTEMS)
    p.add_argument("--profile", action="append",
                   help="profile id (repeatable). omit + pass --all-profiles.")
    p.add_argument("--all-profiles", action="store_true")
    p.add_argument("--sessions", type=int, default=4)
    p.add_argument("--turns", type=int, default=10)
    p.add_argument("--turns-by-session", type=str, default=None)
    p.add_argument("--run-judge", action="store_true",
                   help="run E1 judge inline after the run (default: off).")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    p.add_argument("--fail-fast", action="store_true",
                   help="abort the whole run on the first profile error.")
    p.add_argument(
        "--max-parallel-profiles", type=int, default=4,
        help=(
            "How many profiles to run concurrently against the LLM "
            "backends. vLLM batches requests natively, so N>1 yields a "
            "near-linear speedup until the GPU saturates. Default 4. "
            "Pass 1 to force the original sequential behavior."
        ),
    )
    return p.parse_args(argv)


def _parse_turns_by_session(raw: str | None, sessions: int) -> list[int] | None:
    if raw is None:
        return None
    if not raw.strip():
        raise ValueError("--turns-by-session must not be empty")

    values: list[int] = []
    for idx, part in enumerate(raw.split(","), start=1):
        text = part.strip()
        if not text:
            raise ValueError(
                "--turns-by-session must be a comma-separated list of "
                f"positive integers; item {idx} is empty"
            )
        try:
            turns = int(text)
        except ValueError as exc:
            raise ValueError(
                "--turns-by-session must be a comma-separated list of "
                f"positive integers; item {idx} got {text!r}"
            ) from exc
        if turns <= 0:
            raise ValueError(
                "--turns-by-session values must be positive integers; "
                f"session {idx} got {turns}"
            )
        values.append(turns)

    if len(values) != sessions:
        raise ValueError(
            "--turns-by-session length must equal --sessions "
            f"({len(values)} != {sessions})"
        )
    return values


def _run_one(
    *, profile: ProfileSpec, system: str, run_cfg: RunConfig,
) -> RunArtifactsV6:
    """Dispatch one (profile, system) run.

    All four active systems (v1, v3, v4, v6) go through `run_profile_v6`
    with a per-system turn_fn. This guarantees uniform simulator,
    MISC vocabulary, TTM enum, and 3-field response output across the
    ablation tiers, so MITI / ESC / TTM transition rate / coverage
    metrics are directly comparable.
    """
    client = get_client()
    if run_cfg.run_judge_inline:
        log.warning(
            "--run-judge is a no-op under v6 redesign — the session-level "
            "MITI judge runs automatically at session end."
        )
<<<<<<< HEAD
    if system not in SYSTEMS:
        raise ValueError(f"unknown system {system!r}; expected one of {SYSTEMS}")
    v6_turn_fn = _pick_turn_fn_v6_aligned(system)
    return run_profile_v6(
        profile=profile, run_cfg=run_cfg, client=client,
        system=system, turn_fn=v6_turn_fn,
=======
    if system in ("v1", "v3", "v6", "cami"):
        v6_turn_fn = _pick_turn_fn_v6_aligned(system)
        return run_profile_v6(
            profile=profile, run_cfg=run_cfg, client=client,
            system=system, turn_fn=v6_turn_fn,
        )
    # Legacy v1-v5 path (no longer used by the active matrix; retained
    # for any external caller that still constructs a `system` string
    # outside the ('v1','v3','v6') trio).
    return run_profile(
        profile=profile, system=system, run_cfg=run_cfg,
        client=client, turn_fn=turn_fn,
>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    profiles = _resolve_profiles(args)
<<<<<<< HEAD
=======
    turn_fn = _pick_turn_fn(args.system)
    turns_by_session = _parse_turns_by_session(
        args.turns_by_session, args.sessions,
    )
>>>>>>> 657e5d5 (Add CAMI integration + v6 session updates + LLM routing support)
    run_cfg = RunConfig(
        sessions_per_profile=args.sessions,
        turns_per_session=args.turns,
        turns_by_session=turns_by_session,
        run_judge_inline=args.run_judge,
    )

    parallelism = max(1, min(args.max_parallel_profiles, len(profiles)))
    log.info(
        "starting run: system=%s profiles=%d sessions=%d turns=%s parallel=%d",
        args.system, len(profiles), args.sessions,
        turns_by_session or args.turns, parallelism,
    )

    errors: list[tuple[str, Exception]] = []

    def _do_profile(p: ProfileSpec) -> None:
        _run_one(profile=p, system=args.system, run_cfg=run_cfg)
        log.info("done: profile=%s system=%s", p.profile_id, args.system)

    if parallelism == 1:
        # Sequential path — preserves original ordering and short-circuit
        # behavior on fail_fast.
        for p in profiles:
            try:
                _do_profile(p)
            except Exception as e:
                log.exception("profile %s failed under %s", p.profile_id, args.system)
                errors.append((p.profile_id, e))
                if args.fail_fast:
                    return 2
    else:
        # Profile-level parallelism. Each profile owns its own
        # transcripts/{profile} dir + logs/{profile} subtree, so there's
        # no on-disk contention. The shared LLMClient holds a
        # requests.Session, which is thread-safe under concurrent .post.
        # vLLM continuously batches concurrent requests, so wall-clock
        # scales near-linearly in N until GPU saturates.
        with ThreadPoolExecutor(max_workers=parallelism) as ex:
            futures = {ex.submit(_do_profile, p): p for p in profiles}
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    fut.result()
                except Exception as e:
                    log.exception("profile %s failed under %s", p.profile_id, args.system)
                    errors.append((p.profile_id, e))
                    if args.fail_fast:
                        # Stop accepting new work + cancel pending futures
                        # so we exit promptly on the first error.
                        ex.shutdown(wait=False, cancel_futures=True)
                        return 2

    if errors:
        log.warning("finished with %d profile errors out of %d", len(errors), len(profiles))
        return 1
    log.info("finished cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
