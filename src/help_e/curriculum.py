"""Pre-experiment curriculum builder (Phase B).

Generates ONCE before any experiments:
  - per-profile **eligible_problems** filter (which of the 20-vocab
    problems are plausible for THIS person; one LLM call per profile)
  - **scenario assignment** (chronic / middle / varied — deterministic
    by profile rank; no LLM)
  - per-(profile, session) **seed_problems list** (random subset of
    eligible problems with carry-forward governed by scenario type)
  - per-(profile, session) **session_context** (one LLM call per
    session; the LLM is constrained to use seed_problems exactly and
    receives the previous session's context for chronological
    continuity)

All outputs land under `data/curricula/`:
  data/curricula/scenario_assignments.json     # {profile_id: scenario}
  data/curricula/<profile_id>/eligible_problems.json
  data/curricula/<profile_id>/session_context_s01.json   ... s04.json

At experiment time, every system reads
`data/curricula/<profile_id>/session_context_s<NN>.json` so the v1/v3/v4/v6
ablation sees identical hidden user state per (profile, session).

Routing: all LLM calls in this module use call_role
`curriculum_eligibility` or `curriculum_session_context`, both of
which route to the JUDGE endpoint (llama-3.3-70b on local vLLM) for
deterministic, high-quality, rate-limit-free generation.
"""
from __future__ import annotations

import json
import logging
import random
import textwrap
from pathlib import Path
from typing import Any, Optional

from . import config
from .config import PROBLEM_VOCAB
from .llm_client import CallContext, LLMClient
from .profile_spec import ProfileSpec, list_profiles, load_profile
from .simulator.session_context import (
    SimulatorProfile, run_session_context,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Scenario archetypes (per Phase B §B2 of the plan)
# ---------------------------------------------------------------------------


SCENARIO_TYPES: tuple[str, ...] = ("chronic", "middle", "varied")


# Per-scenario knobs: (carry_forward_ratio, problems_per_session_range).
# Carry-forward ratio = fraction of last session's problems that recur
# in the next session. Lower = more novelty / problem turnover.
SCENARIO_PARAMS: dict[str, dict[str, Any]] = {
    "chronic": {
        "carry_forward_ratio": 0.8,
        "n_problems_range": (3, 4),
    },
    "middle": {
        "carry_forward_ratio": 0.5,
        "n_problems_range": (4, 5),
    },
    "varied": {
        "carry_forward_ratio": 0.2,
        "n_problems_range": (5, 7),
    },
}


def assign_scenario_types(profile_ids: list[str]) -> dict[str, str]:
    """Stratified assignment: split profiles roughly evenly across the
    3 archetypes by profile-id rank.

    Deterministic — running this again with the same `profile_ids` in
    the same order yields the same assignment.
    """
    n = len(profile_ids)
    third = n // 3
    out: dict[str, str] = {}
    for i, pid in enumerate(sorted(profile_ids)):
        if i < third:
            out[pid] = "chronic"
        elif i < 2 * third:
            out[pid] = "middle"
        else:
            out[pid] = "varied"
    return out


# ---------------------------------------------------------------------------
# B1 — eligible problems filter (one LLM call per profile)
# ---------------------------------------------------------------------------


_ELIGIBILITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["eligible_problems", "reasoning"],
    "properties": {
        "eligible_problems": {
            "type": "array",
            "minItems": 5,        # don't let the model degenerate to 1-2
            "items": {"type": "string", "enum": list(PROBLEM_VOCAB)},
            "uniqueItems": True,
        },
        "reasoning": {"type": "string", "minLength": 1},
    },
}


def _eligibility_system_prompt() -> str:
    vocab = "\n".join(f"  - {p}" for p in PROBLEM_VOCAB)
    return textwrap.dedent(f"""\
        You are a curriculum builder for a research chatbot study.
        Given a user profile, you decide WHICH of the 20 wellbeing
        problems below are PLAUSIBLE concerns for this specific
        person. You are NOT predicting which problems they will
        actually have — you are filtering out the ones that don't fit
        their life at all (e.g. `academic_pressure` for a retiree, or
        `caregiver_stress` for a single 22-year-old with no parents
        nearby).

        Be GENEROUS — when in doubt, INCLUDE the problem. The downstream
        curriculum picks subsets from your list to seed individual
        sessions; smaller eligibility lists → less variety. Aim to
        include 8-15 problems unless the profile genuinely rules many
        out.

        # 20-PROBLEM VOCABULARY

        {vocab}

        # OUTPUT SHAPE

        {{
          "eligible_problems": ["academic_pressure", "sleep_problems", ...],
          "reasoning": "Brief 1-2 sentence justification — what about
                       this person's profile makes these problems
                       plausible."
        }}

        Hard rules:
          - Output JSON only. Begin response with `{{`.
          - `eligible_problems` MUST be a subset of the 20-vocabulary
            above. Never invent close-but-off-list strings.
          - At least 5 items. Aim for 8-15.
    """)


def _eligibility_user_prompt(profile: SimulatorProfile) -> str:
    from .simulator.session_context import _format_profile
    return textwrap.dedent(f"""\
        USER PROFILE:
        {_format_profile(profile)}

        Which of the 20 problems are plausible for this person? Filter
        out only the ones that genuinely don't fit their life.
    """)


def pick_eligible_problems(
    *,
    client: LLMClient,
    profile: SimulatorProfile,
) -> dict:
    """B1 — one LLM call per profile. Returns
    `{"eligible_problems": [...], "reasoning": "..."}`.
    Uses the JUDGE endpoint (llama-3.3-70b) via call_role
    `curriculum_eligibility`.
    """
    ctx = CallContext(
        profile_id=profile.profile_id, session_id=0, system="curriculum",
        turn_id=-1, call_role="curriculum_eligibility",
    )
    return client.generate_structured(
        ctx=ctx,
        system_prompt=_eligibility_system_prompt(),
        user_prompt=_eligibility_user_prompt(profile),
        schema=_ELIGIBILITY_SCHEMA,
    )


# ---------------------------------------------------------------------------
# B2 — seed problems per session (carry-forward governed by scenario)
# ---------------------------------------------------------------------------


def pick_seed_problems(
    *,
    eligible: list[str],
    scenario: str,
    prev_seed: Optional[list[str]],
    rng: random.Random,
) -> list[str]:
    """Pick this session's seed problems given:
      - `eligible`: the per-profile filter from B1
      - `scenario`: chronic / middle / varied
      - `prev_seed`: previous session's seed problems (None for s1)
      - `rng`: deterministic RNG seeded per (profile, sweep)

    Carry-forward ratio for the scenario decides how many of prev_seed
    re-appear; the rest are fresh draws from `eligible \\ prev_seed`.
    """
    params = SCENARIO_PARAMS[scenario]
    lo, hi = params["n_problems_range"]
    n = rng.randint(lo, hi)
    n = min(n, len(eligible))   # safety cap

    if prev_seed is None:
        # First session — purely random subset of eligible.
        return sorted(rng.sample(eligible, n))

    carry_ratio = params["carry_forward_ratio"]
    n_carry = max(1, round(carry_ratio * len(prev_seed))) if prev_seed else 0
    n_carry = min(n_carry, len(prev_seed), n)

    carried = rng.sample(prev_seed, n_carry) if n_carry > 0 else []
    pool_for_new = [p for p in eligible if p not in carried]
    n_new = n - len(carried)
    n_new = min(n_new, len(pool_for_new))
    new = rng.sample(pool_for_new, n_new) if n_new > 0 else []

    return sorted(carried + new)


# ---------------------------------------------------------------------------
# B3 — per-(profile, session) session_context pre-bake
# ---------------------------------------------------------------------------


def generate_session_context(
    *,
    client: LLMClient,
    profile: SimulatorProfile,
    session_id: int,
    seed_problems: list[str],
    prev_session_context: Optional[dict],
) -> dict:
    """One LLM call per (profile, session) for B3. Wraps
    `run_session_context` with `use_curriculum_cache=False` (we're
    GENERATING the cache, not reading it) and call_role
    `curriculum_session_context` (routes to JUDGE).
    """
    ctx = CallContext(
        profile_id=profile.profile_id, session_id=session_id,
        system="curriculum", turn_id=-1,
        call_role="curriculum_session_context",
    )
    return run_session_context(
        client=client, ctx=ctx, profile=profile,
        seed_problems=seed_problems,
        prev_session_context=prev_session_context,
        use_curriculum_cache=False,
    )


# ---------------------------------------------------------------------------
# Orchestration helpers
# ---------------------------------------------------------------------------


def _curriculum_dir(profile_id: str) -> Path:
    d = config.CURRICULUM_DIR / profile_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _eligibility_path(profile_id: str) -> Path:
    return _curriculum_dir(profile_id) / "eligible_problems.json"


def _session_context_path(profile_id: str, session_id: int) -> Path:
    return (
        _curriculum_dir(profile_id)
        / f"session_context_s{session_id:02d}.json"
    )


def _scenarios_path() -> Path:
    config.CURRICULUM_DIR.mkdir(parents=True, exist_ok=True)
    return config.CURRICULUM_DIR / "scenario_assignments.json"


def _profile_spec_to_simulator_profile(spec: ProfileSpec) -> SimulatorProfile:
    """Build a `SimulatorProfile` from a `ProfileSpec` YAML, mirroring
    `session_driver_v6._to_simulator_profile` so curriculum gen sees
    the same persona view the runtime simulator uses.
    """
    pd = spec.persona_draft or {}
    cs = pd.get("communication_style", "")
    if isinstance(cs, str):
        cs_list = [s.strip() for s in cs.split(",") if s.strip()] if cs else []
    else:
        cs_list = list(cs)
    return SimulatorProfile(
        profile_id=spec.profile_id,
        demographics=pd.get("demographics", {}) or {},
        personality_traits=list(pd.get("personality_traits", []) or []),
        communication_style=cs_list,
        core_beliefs=list(pd.get("core_beliefs", []) or []),
        hobbies_interests=list(pd.get("hobbies_interests", []) or []),
        relevant_history=pd.get("relevant_history", "") or "",
    )


def generate_curriculum_for_profile(
    *,
    client: LLMClient,
    profile_spec: ProfileSpec,
    scenario: str,
    n_sessions: int = 4,
    skip_existing: bool = True,
) -> dict:
    """Run B1 (eligibility) + B2 (seed selection) + B3 (session_context
    gen) for ONE profile. Writes 1 + n_sessions files to
    `data/curricula/<profile_id>/`.

    Returns a summary dict for logging.
    """
    sim_profile = _profile_spec_to_simulator_profile(profile_spec)
    pid = sim_profile.profile_id

    # B1 — eligible problems (cached on disk).
    elig_path = _eligibility_path(pid)
    if skip_existing and elig_path.exists():
        with elig_path.open() as f:
            elig = json.load(f)
        log.info("B1 cache hit: %s", elig_path)
    else:
        elig = pick_eligible_problems(client=client, profile=sim_profile)
        with elig_path.open("w") as f:
            json.dump(elig, f, indent=2)
        log.info("B1 wrote: %s (%d eligible)", elig_path,
                 len(elig["eligible_problems"]))

    eligible_list = elig["eligible_problems"]

    # B2 — deterministic per-(profile, session) RNG seeded by profile_id.
    rng = random.Random(hash(("curriculum", pid)) & 0xFFFFFFFF)

    prev_ctx: Optional[dict] = None
    prev_seed: Optional[list[str]] = None
    summary: list[dict] = []
    for session_id in range(1, n_sessions + 1):
        seed = pick_seed_problems(
            eligible=eligible_list, scenario=scenario,
            prev_seed=prev_seed, rng=rng,
        )

        # B3 — session_context generation (cached on disk).
        ctx_path = _session_context_path(pid, session_id)
        if skip_existing and ctx_path.exists():
            with ctx_path.open() as f:
                sc = json.load(f)
            log.info("B3 cache hit: %s", ctx_path)
        else:
            sc = generate_session_context(
                client=client, profile=sim_profile,
                session_id=session_id, seed_problems=seed,
                prev_session_context=prev_ctx,
            )
            # Annotate the saved file with the curriculum metadata for
            # downstream auditing (which scenario, what seed went in).
            sc["_curriculum"] = {
                "scenario": scenario,
                "seed_problems": seed,
                "prev_session_id": session_id - 1 if prev_ctx else None,
            }
            with ctx_path.open("w") as f:
                json.dump(sc, f, indent=2, default=str)
            log.info("B3 wrote: %s (active=%s)",
                     ctx_path, sc.get("currently_active_problems"))

        summary.append({
            "session_id": session_id,
            "seed_problems": seed,
            "active_problems": sc.get("currently_active_problems"),
            "fallback": bool(sc.get("_fallback_default")),
        })
        prev_ctx = sc
        prev_seed = sc.get("currently_active_problems") or seed

    return {
        "profile_id": pid,
        "scenario": scenario,
        "eligible_count": len(eligible_list),
        "sessions": summary,
    }


def write_scenario_assignments(profile_ids: list[str]) -> dict[str, str]:
    """Compute + persist `scenario_assignments.json`. Returns the
    {profile_id: scenario} mapping.
    """
    assignments = assign_scenario_types(profile_ids)
    fp = _scenarios_path()
    with fp.open("w") as f:
        json.dump(assignments, f, indent=2, sort_keys=True)
    log.info("wrote %s (%d profiles)", fp, len(assignments))
    return assignments


# ---------------------------------------------------------------------------
# Self-test (no LLM, no IO except temp paths)
# ---------------------------------------------------------------------------


def _self_test() -> None:
    # Scenario assignment is deterministic + balanced.
    pids = [f"P{i:02d}" for i in range(1, 31)]
    a = assign_scenario_types(pids)
    counts = {s: 0 for s in SCENARIO_TYPES}
    for s in a.values():
        counts[s] += 1
    # 30 / 3 = 10 per archetype.
    assert counts == {"chronic": 10, "middle": 10, "varied": 10}, counts
    # Re-running yields the same assignment.
    a2 = assign_scenario_types(pids)
    assert a == a2

    # pick_seed_problems — first session is a random subset of eligible.
    eligible = sorted([
        "academic_pressure", "sleep_problems", "loneliness",
        "low_self_esteem", "general_anxiety", "perfectionism",
        "procrastination", "work_stress",
    ])
    rng = random.Random(0)
    s1 = pick_seed_problems(eligible=eligible, scenario="chronic",
                            prev_seed=None, rng=rng)
    assert 3 <= len(s1) <= 4, f"chronic s1 should be 3-4 problems, got {len(s1)}"
    assert all(p in eligible for p in s1)

    # Chronic carries ~80% of last session.
    rng = random.Random(0)
    s1 = pick_seed_problems(eligible=eligible, scenario="chronic",
                            prev_seed=None, rng=rng)
    s2 = pick_seed_problems(eligible=eligible, scenario="chronic",
                            prev_seed=s1, rng=rng)
    overlap = len(set(s1) & set(s2)) / len(s1)
    assert overlap >= 0.5, f"chronic should carry forward, got {overlap:.2f}"

    # Varied keeps less.
    rng = random.Random(0)
    s1 = pick_seed_problems(eligible=eligible, scenario="varied",
                            prev_seed=None, rng=rng)
    s2 = pick_seed_problems(eligible=eligible, scenario="varied",
                            prev_seed=s1, rng=rng)
    # varied uses 5-7 problems per session; with 8 eligible and 20%
    # carry-forward, overlap should be modest.
    assert len(s1) >= 5

    # Eligibility schema validates a well-formed example.
    from jsonschema import Draft202012Validator
    Draft202012Validator(_ELIGIBILITY_SCHEMA).validate({
        "eligible_problems": list(PROBLEM_VOCAB)[:8],
        "reasoning": "ok",
    })

    print("curriculum self-test PASSED")


if __name__ == "__main__":
    _self_test()
