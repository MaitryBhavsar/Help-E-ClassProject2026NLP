"""Compare V3, V7, V8 on a single profile.

Reads saved transcripts + judge sidecars + graph snapshots from
``transcripts/{profile}/{system}/`` and ``graphs_v6/{system}/`` and
prints a comparison table covering:

  - n_problems_detected (unique across all turns)
  - overlap with the profile's assigned primary problem
  - n_evidences_extracted (size of the graph corpus at end of run)
  - n_evidences_used_total (sum of len(response.evidence_used))
  - n_audits_used_in_response (audit + attribute_connection citations)
  - MITI 4-globals mean (cultivate / soften / partner / empathy)
  - ESC 6-dim mean
  - n_problems_reached_action + pct_reached_action
  - mean_turns_to_action
  - state-progression rate (sum of forward TTM transitions per problem
    per session)

Usage
-----
After running the smoke (``python -m help_e.run --system v3 --profile P01
--turns-list 30,20,20`` etc. for v3, v7, v8), invoke:

    PYTHONPATH=src python -m help_e.eval.compare_v3_v7_v8 \
        --profile P01 --systems v3 v7 v8

Falls back gracefully on missing fields — V3 traces, for example, don't
carry ``n_audits_used_in_response``; the column shows ``n/a`` instead.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from statistics import mean
from typing import Any, Optional

from .. import config
from .metrics import (
    miti_per_session,
    miti_per_profile,
    transition_rate_per_profile,
)


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


# Per-system transcript root overrides set via the CLI ``--root`` flag.
# Maps "v3" / "v7" / "v8" → Path. When unset, falls back to
# ``config.TRANSCRIPT_DIR``.
_ROOT_OVERRIDES: dict[str, Path] = {}


def _system_dir(profile_id: str, system: str) -> Path:
    base = _ROOT_OVERRIDES.get(system, config.TRANSCRIPT_DIR)
    return base / profile_id / system


def _system_graph_dir(system: str) -> Path:
    """Return the graph snapshot directory for a system. When a transcript
    root override is set for the system, derive the graphs root from it
    by replacing ``transcripts`` → ``graphs_v6`` (matches the run
    scripts' output convention).
    """
    base = _ROOT_OVERRIDES.get(system)
    if base is not None:
        # Sibling of "transcripts/" called "graphs_v6/" (the convention
        # used by all three run_*_local_120b.sh scripts).
        return base.parent / "graphs_v6" / system
    return config.GRAPH_V6_DIR / system


def _load_session_files(profile_id: str, system: str) -> list[dict]:
    """Read every ``session_NN.json`` under transcripts/{profile}/{system}/
    in session-id order.
    """
    d = _system_dir(profile_id, system)
    if not d.exists():
        return []
    # Only `session_<digits>.json` — exclude sidecars like
    # `session_context_s01.json` that also match ``session_*``.
    files = sorted(
        f for f in d.glob("session_*.json")
        if re.fullmatch(r"session_\d+\.json", f.name)
    )
    out: list[dict] = []
    for f in files:
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            print(f"[warn] could not read {f}", file=sys.stderr)
    return out


def _load_miti_judges(profile_id: str, system: str) -> list[dict]:
    d = _system_dir(profile_id, system)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("miti_judge_s*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            pass
    return out


def _load_esc_judges(profile_id: str, system: str) -> list[dict]:
    d = _system_dir(profile_id, system)
    if not d.exists():
        return []
    out: list[dict] = []
    for f in sorted(d.glob("esc_judge_s*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except Exception:
            pass
    return out


def _load_final_graph(profile_id: str, system: str) -> Optional[dict]:
    """Load the latest ``{profile}_after_s{NN}.json`` graph snapshot for
    the system. Returns the JSON dict directly (not a graph object —
    we just count things).
    """
    d = _system_graph_dir(system)
    if not d.exists():
        return None
    files = sorted(d.glob(f"{profile_id}_after_s*.json"))
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text())
    except Exception:
        return None


def _flatten_turn_traces(sessions: list[dict]) -> list[dict]:
    """Return every turn_trace across all loaded sessions, in order.
    The shape is system-dependent; metrics functions expect at least
    ``main_problem`` and ``ttm_stage`` per turn.
    """
    out: list[dict] = []
    for s in sessions:
        for tr in s.get("turn_traces") or []:
            out.append(tr)
    return out


def _flatten_turn_responses(sessions: list[dict]) -> list[dict]:
    """Return every turn's ``response`` dict (where Agent 5 wrote
    final_response + evidence_used + used_evidence).
    """
    out: list[dict] = []
    for s in sessions:
        from_traces = [
            tr.get("response")
            for tr in (s.get("turn_traces") or [])
            if isinstance(tr.get("response"), dict) and tr.get("response")
        ]
        if from_traces:
            out.extend(from_traces)
            continue
        for t in s.get("transcript") or []:
            r = t.get("response") if isinstance(t, dict) else None
            if r:
                out.append(r)
    return out


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------


def _normalize_problem_name(mp: Any) -> Optional[str]:
    if isinstance(mp, str):
        return mp.strip() or None
    if isinstance(mp, dict):
        return (mp.get("problem_name") or mp.get("name") or "").strip() or None
    return None


def _count_problems_detected(turn_traces: list[dict]) -> set[str]:
    """Problems appear under ``trace.main_problem``, ``trace.current_problems``, or
    structured inference blocks—not at the turn-trace root.
    """
    seen: set[str] = set()
    for tr in turn_traces:
        trace = tr.get("trace") or {}
        mp = _normalize_problem_name(trace.get("main_problem"))
        if mp:
            seen.add(mp)
        for p in trace.get("current_problems") or []:
            if isinstance(p, str) and p.strip():
                seen.add(p.strip())
            elif isinstance(p, dict):
                pn = p.get("problem_name") or p.get("name")
                if isinstance(pn, str) and pn.strip():
                    seen.add(pn.strip())
        # Legacy/alternate placements
        mp = _normalize_problem_name(tr.get("main_problem"))
        if mp:
            seen.add(mp)
        for p in tr.get("current_problems") or []:
            if isinstance(p, str) and p.strip():
                seen.add(p.strip())
    return seen


def _count_audit_entries(graph: Optional[dict]) -> tuple[int, int]:
    """Return (n_attribute_audit_entries, n_connection_entries) from a
    persisted graph dict. Works for both V6-style and V7-style snapshots.
    """
    if not graph:
        return (0, 0)
    n_attrs = 0
    for prob in (graph.get("problems") or {}).values():
        for la in (prob.get("level_attributes") or {}).values():
            n_attrs += len(la.get("audit_stack") or la.get("evidence_stack") or [])
        for nl in (prob.get("non_level_attributes") or {}).values():
            n_attrs += len(nl.get("audit_stack") or nl.get("evidence_stack") or [])
    n_conn = 0
    for ed in graph.get("edges") or []:
        n_conn += len(ed.get("connection_entries") or [])
    # Older V6 stored cooccurrences instead of structured connection_entries.
    for ed in graph.get("edges") or []:
        n_conn += len(ed.get("cooccurrences") or [])
    return (n_attrs, n_conn)


def _sum_evidence_used(responses: list[dict]) -> int:
    total = 0
    for r in responses:
        ev = r.get("evidence_used") or []
        if isinstance(ev, list):
            total += len(ev)
    return total


def _sum_audits_used(turn_traces: list[dict], responses: list[dict]) -> Optional[int]:
    """Sum of ``trace.n_audits_used_in_response`` across turns, if the
    trace carries it. Returns None for systems that don't expose the
    field (V3/V4 etc.). When None, the table prints ``n/a``.
    """
    counts: list[int] = []
    found = False
    for tr in turn_traces:
        v = tr.get("n_audits_used_in_response")
        if isinstance(v, int):
            counts.append(v)
            found = True
    if not found:
        # Fall back: count attribute + attribute_connection types in
        # responses if the schema includes them.
        for r in responses:
            ev = r.get("evidence_used") or []
            for e in ev:
                if isinstance(e, dict) and e.get("type") in (
                    "attribute", "attribute_connection",
                ):
                    counts.append(1)
                    found = True
        if not found:
            return None
    return sum(counts)


def _miti_summary(judge_outs: list[dict]) -> Optional[dict]:
    """Mean per-global across sessions for one system. Returns None if
    no judge outputs were saved.

    ``miti_per_profile`` consumes the raw session-level judge outputs
    directly (each carries a ``"globals"`` list with name/score per
    global); we pass them as-is.
    """
    if not judge_outs:
        return None
    return miti_per_profile(judge_outs)


def _esc_summary(esc_outs: list[dict]) -> Optional[dict]:
    """ESC: average each dimension over sessions; return mean of means."""
    if not esc_outs:
        return None
    dims = list(config.ESC_DIMENSIONS)
    dim_vals: dict[str, list[float]] = {d: [] for d in dims}
    for j in esc_outs:
        if isinstance(j.get("dimensions"), list):
            for dm in j["dimensions"]:
                if not isinstance(dm, dict):
                    continue
                nam = dm.get("name")
                sco = dm.get("score")
                if nam in dim_vals and isinstance(sco, (int, float)):
                    dim_vals[nam].append(float(sco))
        else:
            # Flat ``scores`` map or legacy top-level ints
            scores = j.get("scores") if isinstance(j.get("scores"), dict) else j
            for d in dims:
                v = scores.get(d) if isinstance(scores, dict) else None
                if isinstance(v, (int, float)):
                    dim_vals[d].append(float(v))
    out: dict[str, Any] = {}
    for d in dims:
        out[d] = round(mean(dim_vals[d]), 2) if dim_vals[d] else None
    populated = [v for v in out.values() if isinstance(v, (int, float))]
    out["mean"] = round(mean(populated), 2) if populated else None
    return out


def _profile_assigned_problem(profile_id: str) -> Optional[str]:
    """Read the profile's ``primary_problem`` from
    ``src/help_e/data/profiles/{profile_id}.yaml``.
    """
    base = Path(__file__).resolve().parents[1] / "data" / "profiles"
    yaml_path = base / f"{profile_id}.yaml"
    if not yaml_path.exists():
        return None
    text = yaml_path.read_text()
    # Tiny inline YAML parse — only need the single ``primary_problem``
    # line, no need to pull in PyYAML.
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("primary_problem:"):
            value = line.split(":", 1)[1].strip()
            return value or None
    return None


# ---------------------------------------------------------------------------
# Per-system summary
# ---------------------------------------------------------------------------


def summarize_system(profile_id: str, system: str) -> dict:
    sessions = _load_session_files(profile_id, system)
    if not sessions:
        return {"system": system, "_no_data": True}

    turn_traces = _flatten_turn_traces(sessions)
    responses = _flatten_turn_responses(sessions)
    final_graph = _load_final_graph(profile_id, system)
    n_attr_audits, n_conn_audits = _count_audit_entries(final_graph)

    detected = _count_problems_detected(turn_traces)
    assigned = _profile_assigned_problem(profile_id)
    overlap = (assigned in detected) if assigned else None

    transitions = transition_rate_per_profile(turn_traces)
    n_problems = transitions["n_problems"]
    n_reached = transitions["n_problems_reached_action"]
    pct_reached = transitions["pct_reached_action"]
    mean_turns_to_action = transitions["mean_turns_to_action"]
    regressions = transitions["regressions_total"]

    # Forward transitions (sum across problems).
    per_problem = transitions["per_problem"]
    n_forward_transitions = sum(
        sum(1 for s in r["first_idx_per_stage"]
            if s in ("contemplation", "preparation", "action"))
        for r in per_problem.values()
    )
    n_sessions = len(sessions)
    n_active_problems_total = sum(
        max(1, n_sessions) for _ in per_problem
    )
    progression_rate = (
        round(n_forward_transitions / n_active_problems_total, 3)
        if n_active_problems_total else None
    )

    miti_judges = _load_miti_judges(profile_id, system)
    esc_judges = _load_esc_judges(profile_id, system)
    miti = _miti_summary(miti_judges)
    esc = _esc_summary(esc_judges)

    return {
        "system": system,
        "n_sessions": n_sessions,
        "n_turns": len(turn_traces),
        "n_problems_detected": len(detected),
        "problems_detected": sorted(detected),
        "assigned_primary_problem": assigned,
        "assigned_overlap": overlap,
        "n_evidences_extracted_attr_audits": n_attr_audits,
        "n_evidences_extracted_connections": n_conn_audits,
        "n_evidences_extracted_total": n_attr_audits + n_conn_audits,
        "n_evidences_used_total": _sum_evidence_used(responses),
        "n_audits_used_in_response": _sum_audits_used(turn_traces, responses),
        "n_problems_reached_action": n_reached,
        "pct_reached_action": pct_reached,
        "mean_turns_to_action": mean_turns_to_action,
        "n_forward_transitions": n_forward_transitions,
        "progression_rate": progression_rate,
        "regressions_total": regressions,
        "miti": miti,
        "esc": esc,
    }


# ---------------------------------------------------------------------------
# Pretty-printing
# ---------------------------------------------------------------------------


def _fmt(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.2f}"
    if isinstance(v, list):
        return ", ".join(map(str, v)) if v else "(none)"
    if isinstance(v, dict):
        # MITI / ESC summary nested dict
        bits = []
        for k in sorted(v):
            bits.append(f"{k}={_fmt(v[k])}")
        return "{" + ", ".join(bits) + "}"
    return str(v)


def print_table(summaries: list[dict], profile_id: str) -> None:
    print()
    print("=" * 78)
    print(f"  Comparison — profile {profile_id}")
    print("=" * 78)
    if not summaries:
        print("  (no system data found)")
        return

    # Choose the rows we display.
    rows = [
        ("sessions",                      "n_sessions"),
        ("turns",                         "n_turns"),
        ("problems detected",             "n_problems_detected"),
        ("  list",                        "problems_detected"),
        ("assigned primary problem",      "assigned_primary_problem"),
        ("  detected by system?",         "assigned_overlap"),
        ("evidences in graph (attr+conn)", "n_evidences_extracted_total"),
        ("  attribute audits",            "n_evidences_extracted_attr_audits"),
        ("  connection entries",          "n_evidences_extracted_connections"),
        ("evidences used (total)",        "n_evidences_used_total"),
        ("  audits used by response",     "n_audits_used_in_response"),
        ("problems reached action",       "n_problems_reached_action"),
        ("  pct reached action",          "pct_reached_action"),
        ("  mean turns to action",        "mean_turns_to_action"),
        ("forward transitions (sum)",     "n_forward_transitions"),
        ("progression rate",              "progression_rate"),
        ("regressions",                   "regressions_total"),
    ]

    # Header
    sys_names = [s["system"] for s in summaries]
    name_w = max(len(label) for label, _ in rows)
    col_w = max(20, max(len(n) for n in sys_names) + 4)
    print(f"  {'metric':<{name_w}}  " + "  ".join(f"{n:>{col_w}}" for n in sys_names))
    print("  " + "-" * (name_w + 2 + (col_w + 2) * len(sys_names)))
    for label, key in rows:
        row_vals = [_fmt(s.get(key)) for s in summaries]
        print(f"  {label:<{name_w}}  " + "  ".join(f"{v:>{col_w}}" for v in row_vals))

    # MITI section
    print()
    print("  MITI 4-globals (per-profile mean across sessions)")
    print("  " + "-" * (name_w + 2 + (col_w + 2) * len(sys_names)))
    for global_key in config.MITI_42_GLOBALS:
        row: list[Any] = []
        for s in summaries:
            miti = s.get("miti")
            if miti and isinstance(miti.get("per_global_mean"), dict):
                row.append(miti["per_global_mean"].get(global_key))
            else:
                row.append(None)
        print(
            f"  {global_key:<{name_w}}  "
            + "  ".join(f"{_fmt(v):>{col_w}}" for v in row)
        )
    overall_row = [
        (s.get("miti") or {}).get("overall_mean") if s.get("miti") else None
        for s in summaries
    ]
    print(
        f"  {'overall_mean':<{name_w}}  "
        + "  ".join(f"{_fmt(v):>{col_w}}" for v in overall_row)
    )

    # ESC section
    print()
    print("  ESC 6-dim (mean across sessions)")
    print("  " + "-" * (name_w + 2 + (col_w + 2) * len(sys_names)))
    for dim in list(config.ESC_DIMENSIONS) + ["mean"]:
        row = [
            (s.get("esc") or {}).get(dim) if s.get("esc") else None
            for s in summaries
        ]
        print(
            f"  {dim:<{name_w}}  "
            + "  ".join(f"{_fmt(v):>{col_w}}" for v in row)
        )

    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="help_e.eval.compare_v3_v7_v8")
    p.add_argument("--profile", required=True,
                   help="Profile ID, e.g. P01")
    p.add_argument("--systems", nargs="+", default=["v3", "v7", "v8"],
                   help="Systems to compare (default: v3 v7 v8)")
    p.add_argument(
        "--root", action="append", default=[],
        metavar="SYSTEM:PATH",
        help=(
            "Override the transcripts root for one system, e.g. "
            "--root v3:output/local_v3_11436_120b/transcripts. May be "
            "passed multiple times. Graph snapshots are read from the "
            "sibling ``graphs_v6/`` directory."
        ),
    )
    p.add_argument("--json", action="store_true",
                   help="Emit raw JSON instead of a printed table")
    args = p.parse_args(argv)

    # Parse --root overrides into _ROOT_OVERRIDES.
    for spec in args.root:
        if ":" not in spec:
            print(f"[error] --root expects SYSTEM:PATH; got {spec!r}",
                  file=sys.stderr)
            return 2
        sys_name, path = spec.split(":", 1)
        _ROOT_OVERRIDES[sys_name.strip()] = Path(path.strip()).resolve()

    summaries: list[dict] = []
    for system in args.systems:
        s = summarize_system(args.profile, system)
        if s.get("_no_data"):
            print(f"[warn] no transcripts found for {args.profile}/{system}",
                  file=sys.stderr)
            continue
        summaries.append(s)

    if not summaries:
        print(f"No data for any of {args.systems} on profile {args.profile}",
              file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"profile": args.profile, "systems": summaries},
                         indent=2, default=str))
    else:
        print_table(summaries, args.profile)
    return 0


if __name__ == "__main__":
    sys.exit(main())
