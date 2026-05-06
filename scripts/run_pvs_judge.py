"""Score saved transcripts with the PVS-6 (Perceived Value of Session) judge.

For each (profile, system, session) under one or more transcript roots,
load the chronological transcript + last-exchange snippets from prior
sessions of the SAME profile/system, call `run_pvs_judge`, and write
``pvs_judge_sNN.json`` next to the existing ``miti_judge_sNN.json``
and ``esc_judge_sNN.json`` files.

Skips a (profile, system, session) if its pvs_judge file already exists,
unless ``--overwrite`` is passed.

Usage
-----
    PYTHONPATH=src python scripts/run_pvs_judge.py \
        --root v3:output/lightning_v3_70b/transcripts \
        --root v7:output/lightning_v7_70b/transcripts \
        --root v8:output/lightning_v8_70b/transcripts \
        [--profile P01 ...] \
        [--max-parallel 4] [--overwrite]

If ``--profile`` is omitted, every profile dir under each root is judged.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

# allow running from repo root with `PYTHONPATH=src`
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from help_e.eval.pvs_judge import run_pvs_judge  # noqa: E402
from help_e.llm_client import CallContext, LLMClient  # noqa: E402


SESSION_FILE_RE = re.compile(r"^session_(\d+)\.json$")


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------


def _list_session_files(prof_dir: Path, system: str) -> list[Path]:
    sys_dir = prof_dir / system
    if not sys_dir.is_dir():
        return []
    files = []
    for f in sys_dir.iterdir():
        m = SESSION_FILE_RE.match(f.name)
        if m:
            files.append((int(m.group(1)), f))
    files.sort(key=lambda x: x[0])
    return [f for _, f in files]


def _extract_transcript(session_blob: dict) -> list[dict]:
    return list(session_blob.get("transcript") or [])


def _extract_first_last_user_assistant(session_blob: dict) -> dict:
    """Extract the opening user message + last user message + last
    assistant message from a session's transcript.
    """
    tx = session_blob.get("transcript") or []
    opening_user = ""
    last_user = ""
    last_assist = ""
    for t in tx:
        role = t.get("role")
        text = (t.get("text") or "").strip()
        if not text:
            continue
        if role == "user":
            if not opening_user:
                opening_user = text
            last_user = text
        elif role == "assistant":
            last_assist = text
    return {
        "session_id": session_blob.get("session_id"),
        "opening_user": opening_user,
        "last_user": last_user,
        "last_assistant": last_assist,
    }


def _pvs_path(session_path: Path) -> Path:
    """Side-by-side: session_NN.json → pvs_judge_sNN.json."""
    m = SESSION_FILE_RE.match(session_path.name)
    if not m:
        raise ValueError(f"unexpected session filename: {session_path.name}")
    sid = int(m.group(1))
    return session_path.parent / f"pvs_judge_s{sid:02d}.json"


# ---------------------------------------------------------------------------
# Per-task work
# ---------------------------------------------------------------------------


def _judge_one(
    *,
    client: LLMClient,
    profile_id: str,
    system: str,
    session_id: int,
    session_blob: dict,
    prior_blobs: list[dict],
    out_path: Path,
    overwrite: bool,
) -> tuple[str, str, int, str]:
    """Returns (status, profile, session_id, msg) tuple — for reporting."""
    if out_path.exists() and not overwrite:
        return ("skip", profile_id, session_id, "already exists")

    transcript = _extract_transcript(session_blob)
    if not transcript:
        return ("skip", profile_id, session_id, "empty transcript")

    prior_snippets = [_extract_first_last_user_assistant(b) for b in prior_blobs]
    ctx = CallContext(
        profile_id=profile_id,
        session_id=session_id,
        system=system,
        turn_id=-1,
        call_role="pvs_judge",
    )
    out = run_pvs_judge(
        client=client,
        ctx=ctx,
        transcript=transcript,
        prior_sessions=prior_snippets,
    )
    out["_profile"] = profile_id
    out["_system"] = system
    out["_session_id"] = session_id
    out_path.write_text(json.dumps(out, indent=2))
    fb = out.get("_fallback_default")
    msg = "fallback" if fb else "ok"
    return ("done", profile_id, session_id, msg)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="run_pvs_judge")
    p.add_argument(
        "--root", action="append", default=[],
        metavar="SYSTEM:PATH",
        help=(
            "transcripts root override per system, e.g. "
            "--root v3:output/lightning_v3_70b/transcripts. Repeat for "
            "multiple systems."
        ),
    )
    p.add_argument(
        "--profile", action="append", default=None,
        help="restrict to specific profile id(s); default: all profiles found.",
    )
    p.add_argument(
        "--max-parallel", type=int, default=4,
        help="max concurrent judge calls (default 4).",
    )
    p.add_argument("--overwrite", action="store_true",
                   help="re-judge sessions that already have a pvs_judge file.")
    p.add_argument("--dry-run", action="store_true",
                   help="list work but don't call the LLM.")
    args = p.parse_args(argv)

    if not args.root:
        p.error("at least one --root SYSTEM:PATH is required")

    # parse roots
    roots: dict[str, Path] = {}
    for spec in args.root:
        if ":" not in spec:
            p.error(f"--root expects SYSTEM:PATH; got {spec!r}")
        sys_name, path = spec.split(":", 1)
        roots[sys_name.strip()] = Path(path.strip()).resolve()

    # Build the work list.
    work: list[dict] = []
    for system, base in roots.items():
        if not base.is_dir():
            print(f"[warn] {base} not found — skipping {system}", file=sys.stderr)
            continue
        # profile dirs under base/
        prof_dirs = sorted([d for d in base.iterdir() if d.is_dir()])
        for prof_dir in prof_dirs:
            pid = prof_dir.name
            if args.profile and pid not in args.profile:
                continue
            sessions = _list_session_files(prof_dir, system)
            if not sessions:
                continue
            # Pre-load all session blobs in chronological order so
            # prior-snippets are easy to assemble.
            blobs: list[dict] = []
            for sf in sessions:
                try:
                    blobs.append(json.loads(sf.read_text()))
                except Exception as e:
                    print(f"[warn] could not parse {sf}: {e}", file=sys.stderr)
                    blobs.append({})
            for i, sf in enumerate(sessions):
                sid_match = SESSION_FILE_RE.match(sf.name)
                if not sid_match:
                    continue
                sid = int(sid_match.group(1))
                out_path = _pvs_path(sf)
                work.append({
                    "system": system,
                    "profile_id": pid,
                    "session_id": sid,
                    "session_blob": blobs[i],
                    "prior_blobs": blobs[:i],
                    "out_path": out_path,
                })

    if not work:
        print("no sessions to judge.")
        return 0

    print(f"found {len(work)} (profile, system, session) tasks across {len(roots)} root(s)")
    if args.dry_run:
        for w in work[:20]:
            print(f"  would judge {w['system']}/{w['profile_id']}/session_{w['session_id']:02d} → {w['out_path']}")
        if len(work) > 20:
            print(f"  ... and {len(work)-20} more")
        return 0

    client = LLMClient()
    started = time.time()
    done = skipped = failed = 0

    with ThreadPoolExecutor(max_workers=args.max_parallel,
                            thread_name_prefix="pvs") as ex:
        futures = [
            ex.submit(_judge_one, client=client,
                      profile_id=w["profile_id"],
                      system=w["system"],
                      session_id=w["session_id"],
                      session_blob=w["session_blob"],
                      prior_blobs=w["prior_blobs"],
                      out_path=w["out_path"],
                      overwrite=args.overwrite)
            for w in work
        ]
        for fut in as_completed(futures):
            try:
                status, pid, sid, msg = fut.result()
            except Exception as e:
                failed += 1
                print(f"  ERROR: {e}", file=sys.stderr)
                continue
            if status == "skip":
                skipped += 1
            elif status == "done":
                done += 1
                if msg == "fallback":
                    failed += 1
            print(f"  [{status}] {pid} s{sid:02d}: {msg}")

    elapsed = time.time() - started
    print(f"\nfinished {done} judged, {skipped} skipped, {failed} fallback "
          f"in {elapsed:.0f}s.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
