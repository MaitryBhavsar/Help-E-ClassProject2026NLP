"""Render a v6 run as readable markdown.

Usage:

    # Print one profile's full session to stdout:
    PYTHONPATH=src python -m help_e.eval.view_profile P01

    # Dump every v6 profile to files under `transcripts/{P}/v6/readable.md`
    # and produce an index at `transcripts/v6_matrix_index.md`:
    PYTHONPATH=src python -m help_e.eval.view_profile --dump-all

The dump shows:
  - profile header (primary_problem, seed situation)
  - hidden session context (simulator framing — NOT fed to the chatbot)
  - full turn-by-turn transcript + what the pipeline inferred / updated
  - final graph state (problems, TTM stages, level attrs, non-level attrs,
    edges with connection evidence)
  - persona inferred at session end
  - Mind-3 satisfaction scores
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

from .. import config
from ..graph_v6 import ProblemGraphV6
from ..profile_spec import load_profile
from .judge import extract_misc_codes
from .v6_loader import (
    list_v6_profiles,
    load_v6_graph,
    load_v6_run_artifacts,
    load_v6_session_files,
)


def _format_problem(g: ProblemGraphV6, name: str) -> str:
    p = g.problems[name]
    out = [
        f"- **{name}** — stage=`{p.current_ttm_stage}`"
        + (f", goal=*{p.goal!r}*" if p.goal else "")
    ]
    if p.level_attributes:
        out.append(f"  - level attrs:")
        for attr, state in p.level_attributes.items():
            latest = state.evidence_stack[-1] if state.evidence_stack else None
            info = latest.inferred_information if latest else ""
            out.append(
                f"    - `{attr}={state.current_level}` ({len(state.evidence_stack)}× evidence): "
                f"{info}"
            )
    if p.non_level_attributes:
        out.append(f"  - non-level attrs:")
        for attr, state in p.non_level_attributes.items():
            latest = state.evidence_stack[-1] if state.evidence_stack else None
            info = latest.inferred_information if latest else ""
            out.append(
                f"    - `{attr}` ({len(state.evidence_stack)}× evidence): {info}"
            )
    return "\n".join(out)


def _format_edge(edge: dict) -> str:
    lines = [
        f"- **{edge['problem_1']} ↔ {edge['problem_2']}** "
        f"(weight={edge.get('weight', 0):.3f}, "
        f"{len(edge.get('cooccurrence_entries') or [])}× cooc, "
        f"{len(edge.get('attribute_connection_entries') or [])}× attr-conn)"
    ]
    for c in (edge.get("attribute_connection_entries") or [])[-3:]:
        lines.append(
            f"  - `{c['relation_type']}` "
            f"{c['attribute_1']} ↔ {c['attribute_2']} "
            f"(conf=`{c['confidence']}`, s{c['session_id']}t{c['turn_id']}): "
            f"{c['connection_explanation']}"
        )
    return "\n".join(lines)


def _format_turn(tr: dict, transcript: list[dict]) -> str:
    tid = tr.get("turn_id")
    user_text = next(
        (t["text"] for t in transcript
         if t.get("role") == "user" and t.get("turn_id") == tid),
        "",
    )
    asst_text = next(
        (t["text"] for t in transcript
         if t.get("role") == "assistant" and t.get("turn_id") == tid),
        "",
    )
    inf = tr.get("inference") or {}
    trace = tr.get("trace") or {}
    resp = tr.get("response") or {}

    # Flags
    fb_parts = []
    if inf.get("_fallback_default"):
        fb_parts.append("INFERENCE FALLBACK")
    if (tr.get("recompute") or {}).get("_fallback_default"):
        fb_parts.append("RECOMPUTE FALLBACK")
    if resp.get("_fallback_default"):
        fb_parts.append("RESPONSE FALLBACK")
    fb_line = f"\n> ⚠ **{'; '.join(fb_parts)}**" if fb_parts else ""

    # Inference outputs
    cur = trace.get("current_problems") or []
    intent = trace.get("user_intent") or "?"
    main = trace.get("main_problem") or "(none)"
    lvl = trace.get("level_updates") or []
    ttm = trace.get("ttm_updates") or []
    cooc = trace.get("cooc_added") or 0
    conn = trace.get("attr_conn_added") or 0

    # Attribute entries inferred this turn
    entries = inf.get("problem_attribute_entries") or []
    entry_lines = []
    for e in entries:
        entry_lines.append(
            f"  - `{e['problem_name']}.{e['attribute_name']}` "
            f"({e['attribute_type']}, conf=`{e['confidence']}`): "
            f"{e['inferred_information']}"
        )
    entry_block = "\n".join(entry_lines) if entry_lines else "  *(none)*"

    # Attr-connections this turn
    conns = inf.get("problem_attribute_connections") or []
    conn_lines = []
    for c in conns:
        conn_lines.append(
            f"  - `{c['relation_type']}` "
            f"{c['problem_1']}.{c['attribute_1']} ↔ "
            f"{c['problem_2']}.{c['attribute_2']} "
            f"(conf=`{c['confidence']}`): {c['connection_explanation']}"
        )
    conn_block = "\n".join(conn_lines) if conn_lines else "  *(none)*"

    # Level + TTM updates
    lvl_lines = [
        f"  - `{u['problem_name']}.{u['attribute_name']}`: "
        f"{u['old_level']} → **{u['new_level']}** — {u['reasoning']}"
        for u in lvl
    ]
    lvl_block = "\n".join(lvl_lines) if lvl_lines else "  *(none)*"
    ttm_lines = [
        f"  - `{u['problem_name']}`: {u['old_ttm_stage']} → **{u['new_ttm_stage']}** — {u['reasoning']}"
        for u in ttm
    ]
    ttm_block = "\n".join(ttm_lines) if ttm_lines else "  *(none)*"

    # Response (v6 redesign: 3-field schema {reasoning, evidence_used,
    # final_response}). MISC codes are named inline in `reasoning`.
    reasoning = resp.get("reasoning", "")
    techs = extract_misc_codes(reasoning)
    tech_str = " ".join(techs) if techs else "(none)"

    return f"""### Turn {tid} — intent `{intent}`, main `{main}`{fb_line}

**User:**
> {user_text}

**Assistant:**
> {asst_text}

**Techniques committed:** `{tech_str}`
**Current problems:** {", ".join(cur) if cur else "(none)"}

<details>
<summary>Inference (what the system inferred from this turn)</summary>

**Attribute entries:**
{entry_block}

**Attribute-connections:**
{conn_block}

**Co-occurrence connections:** {cooc}

</details>

<details>
<summary>Recompute (level + TTM updates after this turn)</summary>

**Level updates:**
{lvl_block}

**TTM updates:**
{ttm_block}

</details>

<details>
<summary>System intent + reasoning (LLM plan for this turn)</summary>

**system_intent:**

> {si}

**response_reasoning:**

> {reasoning}

</details>
"""


def _format_persona(persona: dict) -> str:
    lines = []
    for k, v in persona.items():
        if v in (None, [], ""):
            continue
        if isinstance(v, list):
            v = ", ".join(str(x) for x in v)
        lines.append(f"  - **{k}**: {v}")
    return "\n".join(lines) if lines else "  *(none — persona empty after this session)*"


def _format_mind3(mind3_out: dict) -> str:
    sessions = mind3_out.get("sessions", [])
    if not sessions:
        return "*(no Mind-3 output)*"
    lines = []
    for s in sessions:
        lines.append(f"- Session {s['session_id']}:")
        for d in s.get("dimensions", []):
            lines.append(f"  - `{d['dimension']}`: **{d['score']}**/5")
    return "\n".join(lines)


def render_profile_markdown(profile_id: str) -> str:
    sessions = load_v6_session_files(profile_id)
    if not sessions:
        return f"# {profile_id}\n\n*(no v6 artifacts on disk)*\n"

    run_art = load_v6_run_artifacts(profile_id) or {}
    g = load_v6_graph(profile_id)
    try:
        prof_spec = load_profile(profile_id)
        primary_problem = prof_spec.primary_problem
        seed = prof_spec.seed_situation_paragraph
    except Exception:
        primary_problem = "?"
        seed = ""

    # First session is the only one in these runs
    sess = sessions[0]
    transcript = sess.get("transcript") or []
    turn_traces = sess.get("turn_traces") or []
    ctx_path = (config.TRANSCRIPT_DIR / profile_id / "v6" /
                f"session_context_s{sess['session_id']:02d}.json")
    hidden_ctx = None
    if ctx_path.exists():
        try:
            hidden_ctx = json.loads(ctx_path.read_text())
        except Exception:
            hidden_ctx = None

    parts = []
    parts.append(f"# {profile_id} — primary: `{primary_problem}`\n")
    parts.append(f"**Seed situation:** {seed}\n")

    # Hidden session context
    if hidden_ctx:
        parts.append("## Hidden session context *(simulator-only, NOT seen by chatbot)*\n")
        parts.append(
            f"- **Current life events:** {hidden_ctx.get('current_life_events', '')}\n"
            f"- **Mental state:** {hidden_ctx.get('mental_state', '')}\n"
            f"- **Mood:** {hidden_ctx.get('mood', '')}\n"
            f"- **Emotions:** {', '.join(hidden_ctx.get('emotions') or [])}\n"
            f"- **Resistance / cooperation:** `{hidden_ctx.get('resistance_cooperation_level', '')}`\n"
            f"- **Active problems (hidden):** {', '.join(hidden_ctx.get('currently_active_problems') or [])}\n"
            f"- **Why now:** {hidden_ctx.get('why_bringing_these_up_now', '')}\n"
        )

    # Turn-by-turn
    parts.append("## Session transcript\n")
    for tr in turn_traces:
        parts.append(_format_turn(tr, transcript))

    # Final graph state
    parts.append("## Final graph state (after session 1)\n")
    if g is None:
        parts.append("*(graph snapshot unavailable)*\n")
    else:
        parts.append(f"**Problems ({len(g.problems)}):**\n")
        for name in g.problems:
            parts.append(_format_problem(g, name))
            parts.append("")
        if g.edges:
            parts.append(f"\n**Edges ({len(g.edges)}):**\n")
            graph_dict = g.to_json_dict()
            for e in graph_dict["edges"]:
                parts.append(_format_edge(e))
                parts.append("")
        else:
            parts.append("\n**Edges:** *(none)*\n")

    # Session summary
    summary = sess.get("session_summary") or ""
    if summary:
        parts.append(f"## Session summary (written by summary-agent)\n\n> {summary}\n")

    # Stage transitions
    transitions = sess.get("stage_transitions") or []
    if transitions:
        parts.append("## Stage transitions (pre → post session)\n")
        for t in transitions:
            parts.append(f"- **{t['problem']}**: `{t['from']}` → `{t['to']}`")
        parts.append("")

    # Persona inferred
    parts.append("## Persona inferred at session end\n")
    if g is not None:
        parts.append(_format_persona(g.to_json_dict()["persona"]))
        parts.append("")

    # Mind-3 satisfaction
    mind3 = run_art.get("mind3_out") or {}
    parts.append("## Mind-3 satisfaction ratings\n")
    parts.append(_format_mind3(mind3))
    parts.append("")

    # Mind-2 arc + trajectories
    mind2 = run_art.get("mind2_out") or {}
    if mind2:
        parts.append("## Mind-2 retrospective labels\n")
        for a in mind2.get("arc_summary", []):
            parts.append(f"- **Session {a['session_id']}:** {a['summary']}")
        parts.append("")
        parts.append("### Per-problem trajectories (Mind-2)\n")
        for t in mind2.get("per_problem_trajectories", []):
            parts.append(f"- **{t['problem_name']}**")
            for rec in t.get("trajectory", []):
                parts.append(
                    f"  - Session {rec['session_id']}: "
                    f"`{rec['session_start_stage']}` → `{rec['session_end_stage']}` — {rec.get('notes', '')}"
                )
        parts.append("")

    return "\n".join(parts)


def _index_header_row(profile_id: str) -> str:
    sessions = load_v6_session_files(profile_id)
    if not sessions:
        return f"- `{profile_id}` — (no artifacts)"
    sess = sessions[0]
    g = load_v6_graph(profile_id)
    try:
        prof_spec = load_profile(profile_id)
        primary = prof_spec.primary_problem
    except Exception:
        primary = "?"
    run_art = load_v6_run_artifacts(profile_id) or {}
    mind3 = run_art.get("mind3_out") or {}
    mind3_sessions = mind3.get("sessions", [])
    mind3_overall = None
    if mind3_sessions:
        dims = mind3_sessions[0].get("dimensions", [])
        if dims:
            mind3_overall = sum(d["score"] for d in dims) / len(dims)
    fb = 0
    for tr in sess.get("turn_traces") or []:
        if (tr.get("inference") or {}).get("_fallback_default"):
            fb += 1
        if (tr.get("response") or {}).get("_fallback_default"):
            fb += 1
        if (tr.get("recompute") or {}).get("_fallback_default"):
            fb += 1
    n_problems = len(g.problems) if g else 0
    n_edges = len(g.edges) if g else 0
    m3 = f"{mind3_overall:.2f}" if mind3_overall is not None else "—"
    link = f"{profile_id}/v6/readable.md"
    return (
        f"- [**{profile_id}**]({link}) — primary `{primary}` · "
        f"{n_problems} problems / {n_edges} edges · "
        f"Mind-3 overall **{m3}** · fallbacks {fb}"
    )


def dump_all() -> None:
    profiles = list_v6_profiles()
    if not profiles:
        print("no v6 profiles on disk", file=sys.stderr)
        return
    index_lines = [
        "# v6 matrix — readable index\n",
        f"*Generated from {len(profiles)} profiles in `{config.TRANSCRIPT_DIR}`*\n",
    ]
    for pid in profiles:
        md = render_profile_markdown(pid)
        outdir = config.TRANSCRIPT_DIR / pid / "v6"
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "readable.md").write_text(md)
        index_lines.append(_index_header_row(pid))

    index_path = config.TRANSCRIPT_DIR / "v6_matrix_index.md"
    index_path.write_text("\n".join(index_lines) + "\n")
    print(f"wrote {len(profiles)} profile files under {config.TRANSCRIPT_DIR}")
    print(f"index: {index_path}")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="help_e.eval.view_profile")
    p.add_argument("profile_id", nargs="?", help="profile id; omit with --dump-all")
    p.add_argument("--dump-all", action="store_true",
                   help="write one readable.md per profile + v6_matrix_index.md")
    args = p.parse_args(argv)

    if args.dump_all:
        dump_all()
        return 0
    if not args.profile_id:
        p.error("profile_id is required unless --dump-all is passed")
    print(render_profile_markdown(args.profile_id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
