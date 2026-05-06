"""Classify structured LLM call failures from per-turn JSONL logs.

Buckets HTTP 429 / rate limits, JSON parse issues, schema/validation
errors, and other errors. Counts every log row (each retry attempt) and
also a deduped view: one row per (file, profile_id, session_id, turn_id,
call_role) using the **last attempt** only, so 429 triple-retry does not
triple-count the same failed call.

Usage:
  cd HELP-E && PYTHONPATH=src python -m help_e.eval.diagnose_structured_failures \\
    -g 'output/fireworks_v7_120b/logs/P18/**/turn_*.jsonl' \\
    -g 'output/fireworks_v8_120b/logs/P18/**/turn_*.jsonl'

  # Defaults (same two globs) if -g omitted:
  python -m help_e.eval.diagnose_structured_failures

  python -m help_e.eval.diagnose_structured_failures --json-out /tmp/rca.json
"""
from __future__ import annotations

import argparse
import glob as glob_mod
import json
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_GLOBS = (
    "output/fireworks_v7_120b/logs/P18/**/turn_*.jsonl",
    "output/fireworks_v8_120b/logs/P18/**/turn_*.jsonl",
)

DEFAULT_ROLES = (
    "agent2_inference_v7",
    "agent5_response_v7",
    "agent5_response_v8",
)


def classify_error(error: str | None) -> str:
    if error is None:
        return "success"
    el = error.lower()
    if "429" in error or "too many requests" in el:
        return "rate_limit"
    if "jsondecode" in el or "invalid json" in el:
        return "json_parse"
    if "expecting value" in el or "expecting property" in el:
        return "json_parse"
    if "schema" in el or "validation error" in el or "pydantic" in el:
        return "schema"
    if "json" in el and ("parse" in el or "malformed" in el):
        return "json_parse"
    return "other"


@dataclass
class Example:
    profile_id: str
    session_id: int
    turn_id: int
    call_role: str
    attempt: int
    error_preview: str


@dataclass
class BucketAgg:
    count: int = 0
    examples: list[Example] = field(default_factory=list)

    def add(
        self,
        *,
        profile_id: str,
        session_id: int,
        turn_id: int,
        call_role: str,
        attempt: int,
        error: str | None,
        max_examples: int,
    ) -> None:
        self.count += 1
        if len(self.examples) >= max_examples:
            return
        prev = (error or "")[:200]
        self.examples.append(
            Example(
                profile_id=profile_id,
                session_id=session_id,
                turn_id=turn_id,
                call_role=call_role,
                attempt=attempt,
                error_preview=prev,
            )
        )


def _expand_globs(patterns: list[str]) -> list[Path]:
    out: list[Path] = []
    for pat in patterns:
        if "**" in pat:
            paths = glob_mod.glob(pat, recursive=True)
        else:
            paths = glob_mod.glob(pat)
        for p in sorted(set(paths)):
            out.append(Path(p))
    return out


def _parse_roles(s: str) -> set[str]:
    return {x.strip() for x in s.split(",") if x.strip()}


def analyze_files(
    paths: list[Path],
    roles: set[str],
    *,
    max_examples_per_bucket: int,
) -> dict[str, Any]:
    all_rows: dict[str, BucketAgg] = defaultdict(BucketAgg)
    # dedupe: key -> (attempt, bucket, row dict for example)
    dedupe_best: dict[tuple[str, str, int, int, str], tuple[int, str, dict]] = {}

    malformed_lines = 0
    skipped_role = 0
    total_lines = 0

    for path in paths:
        fkey = str(path.resolve())
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"warn: could not read {path}: {e}", file=sys.stderr)
            continue
        for line_no, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            total_lines += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                malformed_lines += 1
                continue
            cr = row.get("call_role")
            if cr not in roles:
                skipped_role += 1
                continue
            attempt = int(row.get("attempt", 0))
            err = row.get("error")
            if isinstance(err, str) and err == "":
                err = None
            bucket = classify_error(err if isinstance(err, str) or err is None else str(err))

            pid = str(row.get("profile_id", ""))
            sid = int(row.get("session_id", -1))
            tid = int(row.get("turn_id", -1))

            all_rows[bucket].add(
                profile_id=pid,
                session_id=sid,
                turn_id=tid,
                call_role=str(cr),
                attempt=attempt,
                error=err if isinstance(err, str) else None,
                max_examples=max_examples_per_bucket,
            )

            dk = (fkey, pid, sid, tid, str(cr))
            prev = dedupe_best.get(dk)
            if prev is None or attempt >= prev[0]:
                dedupe_best[dk] = (attempt, bucket, row)

    deduped_rows: dict[str, BucketAgg] = defaultdict(BucketAgg)
    for (_fkey, pid, sid, tid, cr), (attempt, bucket, row) in dedupe_best.items():
        err = row.get("error")
        if isinstance(err, str) and err == "":
            err = None
        deduped_rows[bucket].add(
            profile_id=pid,
            session_id=sid,
            turn_id=tid,
            call_role=cr,
            attempt=attempt,
            error=err if isinstance(err, str) else None,
            max_examples=max_examples_per_bucket,
        )

    def serialise(aggs: dict[str, BucketAgg]) -> dict[str, Any]:
        order = ("success", "rate_limit", "json_parse", "schema", "other")
        keys = [k for k in order if k in aggs] + sorted(k for k in aggs if k not in order)
        out: dict[str, Any] = {}
        for k in keys:
            b = aggs[k]
            out[k] = {
                "count": b.count,
                "examples": [
                    {
                        "profile_id": e.profile_id,
                        "session_id": e.session_id,
                        "turn_id": e.turn_id,
                        "call_role": e.call_role,
                        "attempt": e.attempt,
                        "error_preview": e.error_preview,
                    }
                    for e in b.examples
                ],
            }
        return out

    return {
        "files": [str(p) for p in paths],
        "roles": sorted(roles),
        "stats": {
            "total_jsonl_lines": total_lines,
            "malformed_lines": malformed_lines,
            "skipped_wrong_role": skipped_role,
            "matched_lines": total_lines - malformed_lines - skipped_role,
            "deduped_calls": len(dedupe_best),
        },
        "by_bucket_all_attempts": serialise(all_rows),
        "by_bucket_last_attempt_only": serialise(deduped_rows),
    }


def _print_report(data: dict[str, Any]) -> None:
    st = data["stats"]
    print("Files:", len(data["files"]))
    print("Roles:", ", ".join(data["roles"]))
    print(
        f"Lines: total={st['total_jsonl_lines']} matched="
        f"{st['matched_lines']} malformed={st['malformed_lines']} "
        f"skipped_role={st['skipped_wrong_role']}"
    )
    print(f"Deduped calls (last attempt per turn/role): {st['deduped_calls']}")
    print()

    for label, key in (
        ("ALL ATTEMPTS (each retry counts)", "by_bucket_all_attempts"),
        ("LAST ATTEMPT ONLY (per file×profile×session×turn×role)", "by_bucket_last_attempt_only"),
    ):
        print(label)
        print("-" * len(label))
        buckets = data[key]
        for bname, info in buckets.items():
            print(f"  {bname}: {info['count']}")
            for ex in info["examples"]:
                print(
                    f"    e.g. {ex['profile_id']} sess={ex['session_id']} "
                    f"turn={ex['turn_id']} {ex['call_role']} attempt={ex['attempt']}"
                )
                if ex["error_preview"]:
                    ep = ex["error_preview"].replace("\n", " ")[:120]
                    print(f"         {ep}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Classify structured LLM failures in turn_*.jsonl logs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Without -g/--glob, uses default P18 fireworks v7+v8 paths "
            "under output/. Each -g may contain ** for recursion. "
            "Deduped counts use the highest 'attempt' row per "
            "(file, profile_id, session_id, turn_id, call_role)."
        ),
    )
    parser.add_argument(
        "--glob", "-g", action="append", dest="globs", default=None,
        help="Glob for log files (repeatable). Default: built-in P18 v7+v8.",
    )
    parser.add_argument(
        "--roles",
        default=",".join(DEFAULT_ROLES),
        help=f"Comma-separated call_role values (default: {','.join(DEFAULT_ROLES)})",
    )
    parser.add_argument(
        "--examples", type=int, default=5,
        help="Max example rows per bucket (default: 5)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="Write full JSON summary to this path",
    )
    args = parser.parse_args()
    patterns = list(args.globs) if args.globs else list(DEFAULT_GLOBS)
    cwd = Path.cwd()
    paths = _expand_globs(patterns)
    if not paths:
        print("No files matched globs:", patterns, file=sys.stderr)
        print("(cwd:", cwd, ")", file=sys.stderr)
        return 1

    roles = _parse_roles(args.roles)
    data = analyze_files(paths, roles, max_examples_per_bucket=args.examples)
    _print_report(data)

    if args.json_out is not None:
        args.json_out.write_text(json.dumps(data, indent=2), encoding="utf-8")
        print("Wrote", args.json_out)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
