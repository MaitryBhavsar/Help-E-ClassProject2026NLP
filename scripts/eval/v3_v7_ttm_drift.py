"""Find turns where V3 advanced TTM stage while V7 stayed put, then surface
the user message + system response at those turns so the user can judge whether
V3 was being premature (e.g. unsolicited advice when user hasn't asked)."""
import json, glob, sys
from collections import defaultdict

PROFILES = [f"P{i:02d}" for i in range(1, 30, 2)]
STAGE_ORDER = {"(absent)": -1, "precontemplation": 0, "contemplation": 1,
               "preparation": 2, "action": 3, "maintenance": 4}

def load_session(system, profile, session):
    path = f"output/lightning_{system}_70b/transcripts/{profile}/{system}/session_0{session}.json"
    return json.load(open(path))

def per_turn_stages_v3(session_d):
    """Return list of dict {problem: stage} for each turn in the session."""
    out = []
    for tt in session_d["turn_traces"]:
        a3 = tt.get("trace", {}).get("agent3_problem_outputs", {}) or {}
        out.append({p: o.get("current_ttm_stage") for p, o in a3.items() if o})
    return out

def per_turn_stages_v7(session_d):
    """V7 only writes a3b output when TTM was re-evaluated. We carry stage forward."""
    out = []
    last = {}
    for tt in session_d["turn_traces"]:
        cur = dict(last)
        a3b = tt.get("trace", {}).get("agent3b_outputs_per_problem", {}) or {}
        for p, o in a3b.items():
            if o and o.get("current_ttm_stage"):
                cur[p] = o["current_ttm_stage"]
        # Also: if main problem's ttm_stage is set in trace but problem missing in cur, seed
        mp = tt.get("trace", {}).get("main_problem")
        st = tt.get("trace", {}).get("ttm_stage")
        if mp and st and mp not in cur:
            cur[mp] = st
        out.append(cur)
        last = cur
    return out

def trace_stages(profile):
    """For each session 1-3, return list-of-turns with (v3_stages, v7_stages, user_msg, response_text)."""
    sessions = []
    for s in (1, 2, 3):
        try:
            v3d = load_session("v3", profile, s)
            v7d = load_session("v7", profile, s)
        except FileNotFoundError:
            continue
        v3_per_turn = per_turn_stages_v3(v3d)
        v7_per_turn = per_turn_stages_v7(v7d)
        n = min(len(v3d["turn_traces"]), len(v7d["turn_traces"]))
        for i in range(n):
            v3t = v3d["turn_traces"][i]
            v7t = v7d["turn_traces"][i]
            sessions.append({
                "profile": profile,
                "session": s,
                "turn": i + 1,
                "user_msg": v3t.get("user_message", ""),
                "v3_main_problem": v3t.get("trace", {}).get("main_problem"),
                "v7_main_problem": v7t.get("trace", {}).get("main_problem"),
                "v3_stages": v3_per_turn[i],
                "v7_stages": v7_per_turn[i],
                "v3_response": (v3t.get("response") or {}).get("final_response", ""),
                "v7_response": (v7t.get("response") or {}).get("final_response", ""),
                "v3_system_intent": v3t.get("trace", {}).get("system_intent", ""),
                "v7_system_intent": v7t.get("trace", {}).get("system_intent", ""),
                "v3_ttm_reasoning_main": (v3t.get("trace", {}).get("agent3_problem_outputs", {}).get(v3t.get("trace", {}).get("main_problem", ""), {}) or {}).get("ttm_reasoning"),
            })
    return sessions

# Identify forward jumps: where V3's stage for a problem advanced this turn vs last turn,
# but V7's stage for SAME problem stayed put.
def find_drift_examples(profile_traces):
    drifts = []
    by_session = defaultdict(list)
    for row in profile_traces:
        by_session[(row["profile"], row["session"])].append(row)
    for key, turns in by_session.items():
        for i in range(1, len(turns)):
            v3_prev = turns[i-1]["v3_stages"]
            v3_now = turns[i]["v3_stages"]
            v7_prev = turns[i-1]["v7_stages"]
            v7_now = turns[i]["v7_stages"]
            for problem, stage_now in v3_now.items():
                stage_prev_v3 = v3_prev.get(problem, "(absent)")
                if not stage_now or not stage_prev_v3:
                    continue
                if STAGE_ORDER.get(stage_now, -1) > STAGE_ORDER.get(stage_prev_v3, -1):
                    # V3 advanced. Did V7 advance for the same problem?
                    v7_stage_prev = v7_prev.get(problem, "(absent)")
                    v7_stage_now = v7_now.get(problem, "(absent)")
                    if STAGE_ORDER.get(v7_stage_now, -1) <= STAGE_ORDER.get(v7_stage_prev, -1):
                        # V7 stayed put or moved less.
                        drifts.append({
                            **turns[i],
                            "problem": problem,
                            "v3_from": stage_prev_v3,
                            "v3_to": stage_now,
                            "v7_from": v7_stage_prev,
                            "v7_to": v7_stage_now,
                            "v7_held_back": True,
                        })
    return drifts

# Run across all 15 odd profiles
all_drifts = []
for p in PROFILES:
    try:
        traces = trace_stages(p)
    except Exception as e:
        print(f"  {p}: skip ({e})", file=sys.stderr)
        continue
    drifts = find_drift_examples(traces)
    all_drifts.extend(drifts)

print(f"\nFound {len(all_drifts)} turns where V3 advanced TTM but V7 held back.\n")

# Group: V3 advanced TO action/preparation while V7 stayed at precontemplation/contemplation
# These are the most damning ones.
sharpest = [d for d in all_drifts
            if STAGE_ORDER[d["v3_to"]] >= STAGE_ORDER["preparation"]
            and STAGE_ORDER[d["v7_to"]] <= STAGE_ORDER["contemplation"]]

print(f"Of those, {len(sharpest)} are sharp drift (V3 → preparation/action while V7 stays ≤ contemplation).\n")
print("=" * 100)
for d in sharpest[:12]:
    print(f"\n[{d['profile']} s{d['session']} t{d['turn']}] problem={d['problem']}")
    print(f"  V3:  {d['v3_from']:>16} → {d['v3_to']:<16}")
    print(f"  V7:  {d['v7_from']:>16} → {d['v7_to']:<16} (held back)")
    print(f"\n  USER (this turn):\n    {d['user_msg'][:500]}")
    print(f"\n  V3 system_intent:        {d['v3_system_intent']}")
    print(f"  V3 ttm_reasoning (main): {(d.get('v3_ttm_reasoning_main') or '')[:300]}")
    print(f"\n  V3 response:\n    {d['v3_response'][:600]}")
    print(f"\n  V7 system_intent:        {d['v7_system_intent']}")
    print(f"  V7 response:\n    {d['v7_response'][:400]}")
    print("-" * 100)

# Save full data for follow-up
with open("/tmp/v3_v7_drift.json", "w") as f:
    json.dump({"all_drifts": all_drifts, "sharpest": sharpest}, f, indent=2, default=str)
print(f"\n[saved /tmp/v3_v7_drift.json — {len(all_drifts)} drifts, {len(sharpest)} sharp]")
