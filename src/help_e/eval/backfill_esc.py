"""Backfill `esc_judge_s{NN}.json` files from existing session transcripts.

The original v6 matrix used the old multi-session Mind-3 design which
truncated and fell back to constant 3.0 on every call. This script
re-runs the new per-session ESC judge on every (profile, system,
session) transcript already on disk, writing one
`esc_judge_s{NN}.json` next to the existing `session_{NN}.json`.

Usage:
    PYTHONPATH=src python -m help_e.eval.backfill_esc
    PYTHONPATH=src python -m help_e.eval.backfill_esc --systems v1 v3 v6
    PYTHONPATH=src python -m help_e.eval.backfill_esc --max-parallel 8
    PYTHONPATH=src python -m help_e.eval.backfill_esc --skip-existing

Default behavior: overwrites existing `esc_judge_s{NN}.json` (since the
constant-3.0 outputs from the old Mind-3 path won't have those files
anyway, but a prior backfill run would). Pass `--skip-existing` to
short-circuit when the per-session file already exists.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional

from .. import config
from ..llm_client import CallContext, LLMClient
from .esc_judge import run_esc_judge


log = logging.getLogger(__name__)


def _list_transcript_paths(profile_id: str, system: str) -> list[Path]:
    """Return `session_{NN}.json` files for a (profile, system) in order."""
    d = config.TRANSCRIPT_DIR / profile_id / system
    if not d.exists():
        return []
    return sorted(d.glob("session_[0-9]*.json"))


def _extract_transcript(session_file: Path) -> tuple[int, list[dict]]:
    """Return (session_id, transcript) from a session_{NN}.json file."""
    d = json.loads(session_file.read_text())
    return d["session_id"], d.get("transcript") or []


def _backfill_one(
    *,
    client: LLMClient,
    profile_id: str,
    system: str,
    session_file: Path,
    out_path: Path,
) -> dict:
    session_id, transcript = _extract_transcript(session_file)
    if not transcript:
        return {"profile_id": profile_id, "system": system,
                "session_id": session_id, "skipped": True,
                "reason": "empty transcript"}
    ctx = CallContext(
        profile_id=profile_id, session_id=session_id, system=system,
        turn_id=-1, call_role="esc_judge",
    )
    out = run_esc_judge(client=client, ctx=ctx, transcript=transcript)
    out_path.write_text(json.dumps(out, indent=2, default=str))
    return {
        "profile_id": profile_id, "system": system, "session_id": session_id,
        "skipped": False,
        "fallback": bool(out.get("_fallback_default")),
    }


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="help_e.eval.backfill_esc")
    p.add_argument("--systems", nargs="+", default=["v1", "v3", "v6"],
                   help="systems to backfill (default: v1 v3 v6)")
    p.add_argument("--profiles", nargs="*", default=None,
                   help="profile ids (default: all on disk)")
    p.add_argument("--max-parallel", type=int, default=4,
                   help="concurrent ESC calls (default 4)")
    p.add_argument("--skip-existing", action="store_true",
                   help="skip when esc_judge_s{NN}.json already exists")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Discover (profile, system, session_file) triplets.
    if args.profiles:
        profiles = list(args.profiles)
    else:
        # Any profile dir that exists for at least one of the requested systems.
        root = config.TRANSCRIPT_DIR
        seen: set[str] = set()
        for pdir in sorted(root.iterdir()):
            if not pdir.is_dir() or pdir.name.startswith("_"):
                continue
            if any((pdir / s).is_dir() for s in args.systems):
                seen.add(pdir.name)
        profiles = sorted(seen)

    log.info("backfill targets: %d profiles × %d systems = %d (profile,system) pairs",
             len(profiles), len(args.systems), len(profiles) * len(args.systems))

    jobs: list[dict] = []
    for pid in profiles:
        for system in args.systems:
            for sf in _list_transcript_paths(pid, system):
                out_path = sf.parent / sf.name.replace(
                    "session_", "esc_judge_s"
                )
                if args.skip_existing and out_path.exists():
                    continue
                jobs.append({
                    "profile_id": pid, "system": system,
                    "session_file": sf, "out_path": out_path,
                })

    log.info("scheduled %d ESC judge calls (max_parallel=%d)",
             len(jobs), args.max_parallel)
    if not jobs:
        log.info("nothing to do")
        return 0

    client = LLMClient()
    t0 = time.monotonic()
    done = 0
    fallbacks = 0
    with ThreadPoolExecutor(max_workers=args.max_parallel) as ex:
        futures = [
            ex.submit(_backfill_one, client=client, **j) for j in jobs
        ]
        for fut in as_completed(futures):
            try:
                res = fut.result()
            except Exception as e:
                log.exception("job failed: %s", e)
                continue
            done += 1
            if res.get("fallback"):
                fallbacks += 1
            if done % 25 == 0 or done == len(jobs):
                elapsed = time.monotonic() - t0
                rate = done / elapsed if elapsed else 0
                eta = (len(jobs) - done) / rate if rate else 0
                log.info("progress: %d/%d (%.1f/s) fallbacks=%d eta=%.0fs",
                         done, len(jobs), rate, fallbacks, eta)

    elapsed = time.monotonic() - t0
    log.info("done: %d/%d in %.0fs (%.1f/s); fallbacks=%d (%.1f%%)",
             done, len(jobs), elapsed, done/elapsed if elapsed else 0,
             fallbacks, fallbacks/done*100 if done else 0)
    return 0 if fallbacks < len(jobs) * 0.10 else 2


if __name__ == "__main__":
    sys.exit(main())
