"""CLI entrypoint for v6-aligned HELP-E comparison runs.

Examples:
    python -m help_e.run --system v6 --profile P01 --sessions 2 --turns 6
    python -m help_e.run --system cami --profile P01 --sessions 1 --turns 3
    python -m help_e.run --system v1 --all-profiles
"""
from __future__ import annotations

import argparse
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from . import config
from .llm_client import get_client
from .profile_spec import ProfileSpec, RunConfig, list_profiles, load_profile
from .session_driver_v6 import RunArtifactsV6, run_profile_v6


log = logging.getLogger(__name__)


SYSTEMS: tuple[str, ...] = ("v1", "v3", "v6", "cami")
TurnFn = Callable[..., dict[str, Any]]


def _pick_turn_fn(system: str) -> TurnFn:
    """Return the v6-compatible per-turn policy for a system id."""
    if system == "v1":
        from .baselines.v1_history import v1_turn_fn

        return v1_turn_fn
    if system == "v3":
        from .baselines.v3_ttm_from_summary import v3_turn_fn

        return v3_turn_fn
    if system == "v6":
        from .baselines.v6_full import v6_turn_fn

        return v6_turn_fn
    if system == "cami":
        from .baselines.cami_adapter import cami_turn_fn

        return cami_turn_fn
    raise ValueError(f"unknown system {system!r}; expected one of {SYSTEMS}")


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
            "Check the profile data directory."
        )
    return [load_profile(pid) for pid in ids]


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


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="help_e.run")
    parser.add_argument("--system", required=True, choices=SYSTEMS)
    parser.add_argument(
        "--profile",
        action="append",
        help="profile id such as P01; repeat for multiple profiles",
    )
    parser.add_argument("--all-profiles", action="store_true")
    parser.add_argument("--sessions", type=int, default=4)
    parser.add_argument("--turns", type=int, default=10)
    parser.add_argument(
        "--turns-by-session",
        default=None,
        help="comma-separated turn counts, e.g. 3,5,8; length must equal --sessions",
    )
    parser.add_argument(
        "--run-judge",
        action="store_true",
        help="legacy flag; v6-aligned runs already run session judges",
    )
    parser.add_argument(
        "--max-parallel-profiles",
        type=int,
        default=4,
        help="number of profiles to run concurrently; use 1 for sequential debug runs",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="abort the whole run on the first profile error",
    )
    return parser.parse_args(argv)


def _run_one(
    *, profile: ProfileSpec, system: str, run_cfg: RunConfig,
) -> RunArtifactsV6:
    """Run one profile/system pair through the shared v6 driver."""
    if run_cfg.run_judge_inline:
        log.warning(
            "--run-judge is ignored; MITI and ESC judges run automatically "
            "at session end in the v6-aligned driver."
        )

    turn_fn = _pick_turn_fn(system)
    return run_profile_v6(
        profile=profile,
        run_cfg=run_cfg,
        client=get_client(),
        system=system,
        turn_fn=turn_fn,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        turns_by_session = _parse_turns_by_session(
            args.turns_by_session, args.sessions,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    profiles = _resolve_profiles(args)
    run_cfg = RunConfig(
        sessions_per_profile=args.sessions,
        turns_per_session=args.turns,
        turns_by_session=turns_by_session,
        run_judge_inline=args.run_judge,
    )

    parallelism = max(1, min(args.max_parallel_profiles, len(profiles)))
    log.info(
        "starting run: system=%s profiles=%d sessions=%d turns=%s parallel=%d",
        args.system,
        len(profiles),
        args.sessions,
        turns_by_session or args.turns,
        parallelism,
    )

    errors: list[tuple[str, Exception]] = []

    def _do_profile(profile: ProfileSpec) -> None:
        _run_one(profile=profile, system=args.system, run_cfg=run_cfg)
        log.info("done: profile=%s system=%s", profile.profile_id, args.system)

    if parallelism == 1:
        for profile in profiles:
            try:
                _do_profile(profile)
            except Exception as exc:
                log.exception("profile %s failed under %s", profile.profile_id, args.system)
                errors.append((profile.profile_id, exc))
                if args.fail_fast:
                    return 2
    else:
        with ThreadPoolExecutor(max_workers=parallelism) as executor:
            futures = {executor.submit(_do_profile, profile): profile for profile in profiles}
            for future in as_completed(futures):
                profile = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    log.exception("profile %s failed under %s", profile.profile_id, args.system)
                    errors.append((profile.profile_id, exc))
                    if args.fail_fast:
                        executor.shutdown(wait=False, cancel_futures=True)
                        return 2

    if errors:
        log.warning(
            "finished with %d profile errors out of %d",
            len(errors),
            len(profiles),
        )
        return 1

    log.info("finished cleanly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
