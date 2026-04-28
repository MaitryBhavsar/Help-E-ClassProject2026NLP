"""Runtime configuration for HELP-E (§11.7, §18 preamble).

One place for endpoint URL, model name, retrieval/selection knobs, and the
temperature schedule per call_role. All values are overridable via environment
variables so the same code runs against a local Ollama, a remote one, or a
staged replay.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Final

# ---------------------------------------------------------------------------
# LLM endpoint / model
# ---------------------------------------------------------------------------

# Dual-endpoint setup.
#   MAIN  → Lightning AI hosted Llama 3.3 70B (default) — used for the
#           per-turn response, MITI judge, persona update, etc.
#   SIM   → local Ollama serving gpt-oss:20b on port 11438 — used for
#           the simulator (Mind-1, session_context, Mind-3).
# Both backends expose the OpenAI-compatible /v1/chat/completions API,
# so the same client code speaks to either.
#
# Variable and env-var names keep the *_OLLAMA_URL spelling for
# backward compatibility with existing shell scripts (the "OLLAMA" in
# the name is historical — it's just "the OpenAI-compatible base URL").
#
# To switch the MAIN backend back to a local vLLM, override the env vars:
#   export HELPE_MAIN_OLLAMA_URL=http://localhost:11436
#   export HELPE_MAIN_MODEL=meta-llama/Llama-3.3-70B-Instruct
#   export HELPE_VLLM_API_KEY=EMPTY            # any non-empty string ok
#
# Lightning AI defaults below assume HELPE_VLLM_API_KEY is set to a
# valid Lightning AI key. Without that key, MAIN calls will 401.
MAIN_OLLAMA_URL: Final[str] = os.environ.get(
    "HELPE_MAIN_OLLAMA_URL",
    # Default: Lightning AI. Note: NO trailing /v1 — the client appends
    # /v1/chat/completions itself.
    "https://lightning.ai/api",
)
MAIN_MODEL_NAME: Final[str] = os.environ.get(
    "HELPE_MAIN_MODEL",
    # Default: Lightning AI's llama-3.3-70b. Chosen because Lightning's
    # gpt-oss-120b serving had systematic instability (HTTP 500 +
    # corrupted JSON output regardless of throttling) during 2026-04
    # validation. llama-3.3-70b is stable and served fast on the same
    # Lightning AI tenant. Tradeoff: chatbot and judge are now the
    # same model architecture (both llama-3.3-70b) → potential
    # self-rater bias on judge scores. Plan E (full ablation paper
    # sweep) should reconsider gpt-oss-120b once Lightning recovers.
    # To override:
    #   export HELPE_MAIN_MODEL=lightning-ai/gpt-oss-120b
    "lightning-ai/llama-3.3-70b",
)

SIM_OLLAMA_URL: Final[str] = os.environ.get(
    "HELPE_SIM_OLLAMA_URL",
    # Default: Lightning AI hosted gpt-oss-20b — verified to handle
    # mind1_v6 roleplay-as-distressed-user prompts at low concurrency
    # (earlier empty-body issues were load artifacts, not content
    # filtering). 4–5x faster + cheaper than the 120b.
    # Cost: $0.0125/M input + $0.05/M output (essentially free).
    # To switch back to local Ollama:
    #   export HELPE_SIM_OLLAMA_URL=http://localhost:11438
    #   export HELPE_SIM_MODEL=gpt-oss:20b
    "https://lightning.ai/api",
)
SIM_MODEL_NAME: Final[str] = os.environ.get(
    "HELPE_SIM_MODEL",
    "lightning-ai/gpt-oss-20b",
)

# Roles that route to the simulator endpoint. Everything else (the
# main pipeline) defaults to MAIN, except for SMALL_MODEL_ROLES below
# which are dispatched to the same hardware as the simulator (cheap
# 20B-class extraction model) to free the 70B for the response/judge
# calls where voice quality matters most.
SIMULATOR_ROLES: Final[frozenset[str]] = frozenset({
    "mind1", "mind2",
    # v6 simulator additions
    "mind1_v6", "session_context",
})

# Judge roles route to a SEPARATE endpoint so judging happens on
# llama-3.3-70b regardless of what the chatbot side is using. Without
# this split, swapping the chatbot model (e.g. gpt-oss-120b for the v3
# baseline) would also swap the judge, which conflates system effect
# with judge-model effect.
JUDGE_ROLES: Final[frozenset[str]] = frozenset({
    "miti_judge", "esc_judge",
    # Curriculum-generation roles (one-time pre-experiment): route to
    # the JUDGE endpoint for high-quality + zero rate-limit competition
    # with chatbot calls. These NEVER fire during a matrix run — they
    # only fire from `scripts/generate_curriculum.py`.
    "curriculum_eligibility",      # B1 — per-profile eligible-problems filter
    "curriculum_session_context",  # B3 — pre-bake session_context per (profile, session)
})

# Judge endpoint (3rd tier alongside MAIN + SIM). Default: Lightning AI
# llama-3.3-70b — same model + provider as MAIN. Both judge and chatbot
# now use the same model after Lightning's gpt-oss-120b proved unstable.
#
# To switch judges to a local vLLM (no quota, fully offline):
#   export HELPE_JUDGE_OLLAMA_URL=http://localhost:11436
#   export HELPE_JUDGE_MODEL=meta-llama/Llama-3.3-70B-Instruct
JUDGE_OLLAMA_URL: Final[str] = os.environ.get(
    "HELPE_JUDGE_OLLAMA_URL", "https://lightning.ai/api",
)
JUDGE_MODEL_NAME: Final[str] = os.environ.get(
    "HELPE_JUDGE_MODEL", "lightning-ai/llama-3.3-70b",
)

# Small-model routing tier (third route alongside main + simulator).
# By default these chatbot-side roles are sent to the same endpoint as
# the simulator — gpt-oss:20b on port 11438 — because they are
# structured-extraction tasks (parse → typed output) that don't need
# the 70B's reasoning depth. response_v6 + miti_judge_v6 stay on the
# 70B to preserve voice / judgment quality. Override the URL/model
# independently if you spin up a separate small-model server.
SMALL_MODEL_URL: Final[str] = os.environ.get(
    "HELPE_SMALL_URL", os.environ.get("HELPE_SIM_OLLAMA_URL", "http://localhost:11438"),
)
SMALL_MODEL_NAME: Final[str] = os.environ.get(
    "HELPE_SMALL_MODEL", os.environ.get("HELPE_SIM_MODEL", "gpt-oss:20b"),
)
# NOTE: temporarily empty — a calibration smoke (gpt-oss:20b serving
# both mind1_v6 AND inference/recompute) showed mind1_v6 hitting 4/5
# fallbacks because Ollama defaults to single-threaded request handling
# and saturates with 5 roles routed to the same backend. Re-populate
# this set (and restart Ollama with `OLLAMA_NUM_PARALLEL=4` or higher)
# to re-enable small-model routing without the saturation penalty.
SMALL_MODEL_ROLES: Final[frozenset[str]] = frozenset()

# Back-compat aliases so older callsites that read ``OLLAMA_URL`` /
# ``MODEL_NAME`` still see the main endpoint.
OLLAMA_URL: Final[str] = MAIN_OLLAMA_URL
MODEL_NAME: Final[str] = MAIN_MODEL_NAME

# Request timeout; long CoT prompts (merged response, Mind-2) can take a while.
REQUEST_TIMEOUT_S: Final[int] = int(os.environ.get("HELPE_TIMEOUT_S", "600"))

# Bearer token for the MAIN OpenAI-compatible endpoint.
# - For Lightning AI (default): set HELPE_VLLM_API_KEY to your Lightning
#   AI API key (the "9c9c..." UUID-shaped token from your dashboard).
#   Without it, MAIN calls return 401.
# - For local vLLM launched without --api-key: any non-empty string
#   works; "EMPTY" is the conventional placeholder.
# (The env-var name keeps its historical "VLLM" spelling for back-compat
# with existing shell scripts; the value is just a generic bearer token.)
API_KEY: Final[str] = os.environ.get("HELPE_VLLM_API_KEY", "EMPTY")

# Per-endpoint API keys. The current production setup spans THREE
# tenants:
#   - MAIN  (chatbot, gpt-oss-120b)  — Lightning AI account A
#   - SIM   (mind1+session_context, gpt-oss-20b) — Lightning AI account B
#                                       (different from MAIN)
#   - JUDGE (miti+esc, llama-3.3-70b) — local vLLM (no key needed)
#
# Each endpoint falls back to the global `API_KEY` when its own env var
# isn't set, preserving back-compat with single-key setups.
#
# Set independently:
#   export HELPE_MAIN_API_KEY=<gpt-oss-120b account key>
#   export HELPE_SIM_API_KEY=<gpt-oss-20b account key>     # different from MAIN
#   export HELPE_JUDGE_API_KEY=EMPTY                       # local vLLM
MAIN_API_KEY: Final[str] = os.environ.get(
    "HELPE_MAIN_API_KEY", API_KEY,
)
SIM_API_KEY: Final[str] = os.environ.get(
    "HELPE_SIM_API_KEY", MAIN_API_KEY,
)
JUDGE_API_KEY: Final[str] = os.environ.get(
    "HELPE_JUDGE_API_KEY", "EMPTY",
)

# ---------------------------------------------------------------------------
# Retrieval / selection knobs (§6.5, §6.6)
# ---------------------------------------------------------------------------

# Last-N-turn window consumed by extraction, retrieval, and HBM modifiers.
LAST_N_TURNS: Final[int] = int(os.environ.get("HELPE_N", "5"))

# Top-K recent stack entries surfaced per edge in the bundle.
TOP_K_STACK: Final[int] = int(os.environ.get("HELPE_K", "3"))

# Closeness parameters (§6.5 Step B).
CLOSENESS_ALPHA: Final[float] = float(os.environ.get("HELPE_ALPHA", "1.0"))
CLOSENESS_THRESHOLD: Final[float] = float(os.environ.get("HELPE_THRESHOLD", "2.0"))

# Response style caps (§18.4, system prompt).
MAX_RESPONSE_SENTENCES: Final[int] = 5
MAX_RESPONSE_QUESTIONS: Final[int] = 1

# ---------------------------------------------------------------------------
# Temperature schedule per call_role (§18 preamble)
# ---------------------------------------------------------------------------

TEMPERATURE_BY_ROLE: Final[dict[str, float]] = {
    "extraction": 0.2,
    "level_recompute": 0.2,
    "ttm_infer": 0.2,
    "merged_response": 0.4,
    "persona_update": 0.2,
    "session_summary": 0.2,
    "mind1": 0.4,
    "mind2": 0.2,
    "e1_judge": 0.0,
    # Baseline-specific roles
    "v1_response": 0.4,
    "v2_summary_update": 0.2,
    "v2_response": 0.4,
    "v3_ttm_from_summary": 0.2,
    "v3_response": 0.4,
    # v6 roles (problem-centric graph pipeline)
    "inference": 0.2,
    "recompute": 0.2,
    "response_v6": 0.4,
    "response_simple": 0.4,   # shared response prompt for v1/v3/v4
    "persona_update_v6": 0.2,
    # v6 simulator roles
    "mind1_v6": 0.6,       # higher to diversify user utterances
    "session_context": 0.5,
    # Session-level judges (renamed from miti_judge_v6 / esc_judge_v6;
    # mind3 dropped — esc_judge replaces it).
    "miti_judge": 0.0,
    "esc_judge": 0.0,
    # v6-aligned baselines (v1, v3, v4) share the v6 simulator; v3 has
    # one combined extract+summary+TTM call per turn, v4 adds connections.
    "v3_extract": 0.2,
    "v4_extract": 0.2,
    # Curriculum gen — one-time, quality-critical. Eligibility is
    # deterministic (filter), context gen wants some variety across
    # sessions for the same profile.
    "curriculum_eligibility": 0.0,
    "curriculum_session_context": 0.5,
}

# Ordered enum of every call_role used by §11.7 seed derivation.
CALL_ROLES: Final[tuple[str, ...]] = tuple(TEMPERATURE_BY_ROLE.keys())

# Seed prefix so multiple runs of the same (profile, session, system, turn, role)
# can be deliberately re-seeded by bumping this.
SEED_SALT: Final[str] = os.environ.get("HELPE_SEED_SALT", "helpe-v1")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PACKAGE_ROOT: Final[Path] = Path(__file__).parent
LOG_ROOT: Final[Path] = Path(os.environ.get("HELPE_LOG_ROOT", str(PACKAGE_ROOT / "logs")))
PROFILE_DIR: Final[Path] = PACKAGE_ROOT / "data" / "profiles"
# Pre-baked per-(profile, session) curricula (eligible_problems +
# stratified scenario assignments + frozen session_context per session).
# Generated ONCE by `scripts/generate_curriculum.py` before any
# experiments and read by every system at session start so the v1/v3/v4/v6
# ablation sees identical hidden user state per (profile, session).
CURRICULUM_DIR: Final[Path] = Path(
    os.environ.get("HELPE_CURRICULUM_DIR", str(PACKAGE_ROOT / "data" / "curricula"))
)
GRAPH_DIR: Final[Path] = Path(os.environ.get("HELPE_GRAPH_DIR", str(PACKAGE_ROOT / "graphs")))
TRANSCRIPT_DIR: Final[Path] = Path(
    os.environ.get("HELPE_TRANSCRIPT_DIR", str(PACKAGE_ROOT / "transcripts"))
)

# ---------------------------------------------------------------------------
# Enums (source of truth for prompt schemas)
# ---------------------------------------------------------------------------

TTM_STAGES: Final[tuple[str, ...]] = (
    "precontemplation", "contemplation", "preparation", "action", "maintenance",
)

USER_INTENTS: Final[tuple[str, ...]] = (
    "vent", "seek_validation", "seek_advice", "explore_option",
    "report_progress", "check_in", "other",
)

ATTR_TYPES: Final[tuple[str, ...]] = (
    "perceived_severity", "perceived_susceptibility", "perceived_benefits",
    "perceived_barriers", "self_efficacy", "cues_to_action", "motivation",
    "coping_strategies", "past_attempts", "triggers", "goal",
)

PERSONA_FIELDS: Final[tuple[str, ...]] = (
    "demographics", "personality_traits", "core_values", "core_beliefs",
    "support_system", "hobbies_interests", "communication_style",
    "relevant_history",
)

LEVELS: Final[tuple[str, ...]] = ("low", "moderate", "high")

ESC_DIMENSIONS: Final[tuple[str, ...]] = (
    "empathy", "understanding", "helpfulness", "autonomy_respect",
    "non_judgment", "willingness_to_continue",
)

JUDGE_DIMENSIONS: Final[tuple[str, ...]] = (
    "reflect_vs_direct", "autonomy_support", "no_unsolicited_advice",
    "open_vs_closed_questions",
)

# 12 MI techniques (T1–T12). Label used in prompts; id used for equality.
MI_TECHNIQUES: Final[dict[str, str]] = {
    "T1": "Reflection",
    "T2": "Affirmation",
    "T3": "Summary",
    "T4": "Open Question",
    "T5": "Ask Permission",
    "T6": "Options Menu",
    "T7": "EPE",
    "T8": "Change Talk",
    "T9": "Confidence Builder",
    "T10": "Action Planning",
    "T11": "Coping/Relapse Plan",
    "T12": "Normalize/Reframe",
}

# 20-problem vocabulary (§8.2) — loaded lazily from problems.md at runtime if
# the file is edited, but hardcoded here as the enum of record for schemas.
PROBLEM_VOCAB: Final[tuple[str, ...]] = (
    "academic_pressure", "work_stress", "sleep_problems", "procrastination",
    "general_anxiety", "low_self_esteem", "perfectionism", "social_anxiety",
    "loneliness", "conflicts_with_partner", "breakup_aftermath",
    "conflicts_with_parents", "conflicts_with_friends", "financial_stress",
    "career_uncertainty", "caregiver_stress", "grief_of_loved_one",
    "health_anxiety", "body_image_concerns", "life_transition",
)

# ---------------------------------------------------------------------------
# v6 additions — problem-centric graph with typed problem-problem edges.
# See docs/technical_report.md and the Phase-A plan. v6 does NOT replace v1–v5
# constants above; it lives alongside them so the existing ablation matrix
# stays frozen.
# ---------------------------------------------------------------------------

# Split of ATTR_TYPES into leveled (7) vs non-leveled (4).
LEVEL_ATTR_TYPES: Final[tuple[str, ...]] = (
    "perceived_severity", "perceived_susceptibility", "perceived_benefits",
    "perceived_barriers", "self_efficacy", "cues_to_action", "motivation",
)
NON_LEVEL_ATTR_TYPES: Final[tuple[str, ...]] = (
    "coping_strategies", "past_attempts", "triggers", "goal",
)

# v6 uses {low, medium, high, unknown}. v1–v5 stays on the 3-level `LEVELS`
# ("low", "moderate", "high") above. Note the spelling shift: v6 uses
# "medium" (per the v6 spec) while v1–v5 uses "moderate" (legacy).
LEVELS_V6: Final[tuple[str, ...]] = ("low", "medium", "high", "unknown")

# Problem-problem edge relation types used by attribute-connection entries.
RELATION_TYPES: Final[tuple[str, ...]] = (
    "causal", "effect", "reinforcing", "conflicting",
    "shared_trigger", "shared_barrier", "shared_goal", "unclear_but_related",
)

# Confidence scale + weights used in edge-weight computation.
CONFIDENCE_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high")
CONFIDENCE_WEIGHT: Final[dict[str, float]] = {
    "low": 0.5, "medium": 0.75, "high": 1.0,
}

# Resistance/cooperation scale for the v6 user simulator.
RESISTANCE_LEVELS: Final[tuple[str, ...]] = ("low", "medium", "high")

# Retrieval / edge-weight knobs for v6.
TOP_S_NEIGHBORS: Final[int] = int(os.environ.get("HELPE_TOP_S", "2"))
EDGE_WEIGHT_ALPHA_M: Final[float] = float(os.environ.get("HELPE_EDGE_ALPHA_M", "0.4"))
EDGE_WEIGHT_ALPHA_A: Final[float] = float(os.environ.get("HELPE_EDGE_ALPHA_A", "0.6"))
RECENCY_HALF_LIFE_TURNS: Final[int] = int(os.environ.get("HELPE_RECENCY_HL", "20"))

# (session_id, turn_id) → monotone global turn index for recency math.
# Assumes < SESSION_TURN_STRIDE turns per session (current runs use 10).
SESSION_TURN_STRIDE: Final[int] = 1000

# v6 graph snapshots persist here, kept separate from v1–v5 graphs/.
GRAPH_V6_DIR: Final[Path] = Path(
    os.environ.get("HELPE_GRAPH_V6_DIR", str(PACKAGE_ROOT / "graphs_v6"))
)

# ---------------------------------------------------------------------------
# v6 REDESIGN — MISC-aligned vocabulary, 8-value user_intent, 4-stage TTM.
# Lives alongside v1–v5 enums (which stay frozen). v6 modules import the
# `_V6` variants below. See plan at
# /Users/maitry/.claude/plans/i-want-u-to-lovely-boot.md
# ---------------------------------------------------------------------------

# TTM stages (v6 redesign): drop `maintenance` since we don't track sustained
# behavior change across weeks/months. (M&R 2013 ch. 18; TIP 35 ch. 4.)
TTM_STAGES_V6: Final[tuple[str, ...]] = (
    "precontemplation", "contemplation", "preparation", "action",
)

# user_intent (v6 redesign): 8 values, constructed HELP-E taxonomy informed
# by M&R 2013, MISC client codes (Moyers et al. 2014), Cutrona & Suhr 1992,
# ESConv (Liu et al. 2021).
USER_INTENTS_V6: Final[tuple[str, ...]] = (
    "express_emotion",
    "seek_validation",
    "seek_information",
    "deliberate_decision",
    "request_plan",
    "report_action",
    "resistance",
    "small_talk",
)

# Definitions for the 8 user_intent values. Used in the inference prompt
# and as the entry-style reference in the response prompt.
USER_INTENTS_V6_DEF: Final[dict[str, str]] = {
    "express_emotion": "User expresses affect or distress without (yet) asking for help",
    "seek_validation": "User wants their feeling/reaction confirmed as reasonable",
    "seek_information": "User wants facts, context, or perspective",
    "deliberate_decision": "User is weighing options or sitting with ambivalence",
    "request_plan": "User asks what to do / next step",
    "report_action": "User reports an attempt, slip, or progress",
    "resistance": "User pushes back, disagrees, expresses reluctance",
    "small_talk": "Session opening, closing, or pleasantries",
}

# Per-intent entry style — how the chatbot's response should BEGIN given
# this intent. NOT a source of MI strategies (those come from TTM).
INTENT_ENTRY_STYLE_V6: Final[dict[str, str]] = {
    "express_emotion": "Reflect first; defer planning/info.",
    "seek_validation": "Normalize their reaction; affirm specifically.",
    "seek_information": "Elicit what they know → provide one piece → elicit reaction.",
    "deliberate_decision": "Reflect both sides of the ambivalence before any nudge.",
    "request_plan": "Propose ONE small step matched to TTM stage. Don't deflect with a question.",
    "report_action": "Affirm the specific effort; respond to the content of the report.",
    "resistance": "Roll with it — reflect resistance without arguing; emphasize autonomy.",
    "small_talk": "Stay light; warm acknowledgment.",
}

# OARS — the four basic MI skills (M&R 2013 ch. 6). Always-on baseline,
# referenced in the SYSTEM prompt; NOT in the per-turn candidate list.
OARS_SKILLS: Final[dict[str, str]] = {
    "open_question": "Open Question — invite elaboration, don't interrogate",
    "affirm": "Affirmation — spotlight a SPECIFIC effort, never generic praise",
    "simple_reflection": "Simple Reflection — name what's UNDERNEATH the user's words",
    "summary": "Summary — gather threads at natural breakpoints",
}

# MI principle (M&R 2013 ch. 2–3, MI Spirit). Always-on, NOT selectable.
MI_PRINCIPLES_V6: Final[dict[str, str]] = {
    "emphasize_control": "Autonomy support — the user owns their choices",
}

# Selectable MISC strategy codes (10) — picked per turn from candidate list.
# Each carries a TTM transition function derived from TIP 35 ch. 4 +
# M&R 2013 ch. 18.
MISC_CODES: Final[dict[str, dict[str, str]]] = {
    "support": {
        "label": "Support",
        "what": "Sympathetic / understanding statement",
        "transition_fn": "Sustains engagement at any stage; especially during distress / resistance",
    },
    "facilitate": {
        "label": "Facilitate",
        "what": "Brief encouragement to continue ('mm-hmm', 'go on')",
        "transition_fn": "Sustains conversation flow at any stage",
    },
    "complex_reflection": {
        "label": "Complex Reflection",
        "what": "Reflect with added inferred meaning, depth, or shift in emphasis",
        "transition_fn": "Develops discrepancy → drives precontemplation → contemplation",
    },
    "reframe": {
        "label": "Reframe",
        "what": "Offer alternative interpretation that widens or shifts meaning",
        "transition_fn": "Widens stuck frame → drives precontemplation → contemplation",
    },
    "inform_with_permission": {
        "label": "Inform (with permission)",
        "what": "Provide information after asking permission",
        "transition_fn": "Builds awareness (pre → contempl) or supports informed decision (contempl → prep)",
    },
    "raise_concern_with_permission": {
        "label": "Raise Concern (with permission)",
        "what": "Voice concern about a plan or pattern after asking permission",
        "transition_fn": "Develops discrepancy gently (pre → contempl) or surfaces risks to refine plan (prep → action)",
    },
    "evoke": {
        "label": "Evoke",
        "what": "Invite client's own reasons for change in their words (counselor-side mirror of MISC client 'Change Talk')",
        "transition_fn": "Core MI move for resolving ambivalence → drives contemplation → preparation",
    },
    "closed_question": {
        "label": "Closed Question",
        "what": "Question that limits answer to yes/no/short — used sparingly",
        "transition_fn": "Confirms commitment or specific plan elements (prep → action)",
    },
    "advise_with_permission": {
        "label": "Advise (with permission)",
        "what": "Suggest a course of action after asking permission",
        "transition_fn": "Drives preparation → action; supports change planning",
    },
    "structure": {
        "label": "Structure",
        "what": "Explain what will happen next in the session or turn",
        "transition_fn": "Drives preparation → action by structuring the change plan",
    },
}

# MI-inconsistent MISC codes — anti-patterns. Listed in the SYSTEM prompt
# as things NOT to do. NOT selectable. (Moyers et al. 2014 catalogues these
# as MI-non-adherent counselor behaviors.)
MISC_INCONSISTENT_CODES: Final[dict[str, str]] = {
    "direct": "Giving an order / command — violates MI autonomy support",
    "confront": "Arguing, criticizing, disagreeing — known to increase resistance",
    "warn": "Threatening consequences — MI-inconsistent",
    "advise_without_permission": "Suggestion not gated by permission",
    "inform_without_permission": "Information not gated by permission",
    "raise_concern_without_permission": "Concern not gated by permission",
}

# Candidate-selection rule: TTM stage → MISC strategy candidates.
# Pure rule, no LLM scan. Used by mi_selector_v6.select_candidates_v6.
TTM_TO_MISC_COMMON: Final[tuple[str, ...]] = ("support", "facilitate")

TTM_TO_MISC_STAGE_SPECIFIC: Final[dict[str, tuple[str, ...]]] = {
    "precontemplation": (
        "complex_reflection", "reframe",
        "inform_with_permission", "raise_concern_with_permission",
    ),
    "contemplation": (
        "evoke", "complex_reflection", "inform_with_permission",
    ),
    "preparation": (
        "advise_with_permission", "closed_question", "structure",
    ),
    "action": (
        "structure", "advise_with_permission", "raise_concern_with_permission",
    ),
}

# Per-stage transition target — what the system is trying to move the user
# toward when they are in a given stage. Surfaced in the response prompt's
# CANDIDATE STRATEGIES block as "(target: X → Y)".
TTM_TRANSITION_TARGET_V6: Final[dict[str, str]] = {
    "precontemplation": "precontemplation → contemplation",
    "contemplation": "contemplation → preparation",
    "preparation": "preparation → action",
    "action": "sustain action",
}

# K cap on stacked attribute entries used per (problem, attribute) by the
# recompute prompt for level-recomputation (post-walkthrough audit item).
RECOMPUTE_K_PAST_ENTRIES: Final[int] = 5

# How many past turns' (main_problem + strategies) are surfaced to the
# response prompt for diversity (the "PAST TWO TURNS" section).
PAST_TURNS_FOR_DIVERSITY: Final[int] = 2

# v6 evaluation — MITI 4.2 globals (session-level) replace the old 4-dim
# rubric.
MITI_42_GLOBALS: Final[tuple[str, ...]] = (
    "cultivating_change_talk",
    "softening_sustain_talk",
    "partnership",
    "empathy",
)
MITI_42_GLOBALS_DEF: Final[dict[str, str]] = {
    "cultivating_change_talk": "Encouraging the client's own arguments for change",
    "softening_sustain_talk": "Avoiding arguing against the client's resistance",
    "partnership": "Collaborative stance vs. expert-on-pedestal",
    "empathy": "Accurate understanding of the client's perspective",
}

# Legacy adds dict (was used to inject v6-redesign judge role into
# TEMPERATURE_BY_ROLE without modifying the main table). Now folded
# into TEMPERATURE_BY_ROLE directly. Kept empty for back-compat with
# any code that imports it.
TEMPERATURE_BY_ROLE_V6_ADDS: Final[dict[str, float]] = {}

# Per-role retry budget that overrides LLMClient.max_retries (default 1
# = 2 attempts). response_v6 has strict validators (banned-opener,
# candidate-subset, sentence/question caps) that benefit from more
# retries; the LLM uses the previous attempt's error hint to fix.
MAX_RETRIES_BY_ROLE: Final[dict[str, int]] = {
    # Sane defaults for stable Lightning llama-3.3-70b serving:
    # 2 retries (3 attempts total) covers transient JSON parse errors
    # without burning excessive quota on persistent failures.
    "response_v6": 2,
    "response_simple": 2,
    "inference": 2,
    "v3_extract": 2,
    "v4_extract": 2,
    "recompute": 2,
    # 0% fallback in live matrix; smaller budget is fine.
    "persona_update_v6": 3,
    "miti_judge": 3,
    # ESC judge runs 1 call per session × 4 sessions × N systems jobs.
    # Bumped to 5 retries to cover ~120s of backoff under load.
    "esc_judge": 5,
    "session_context": 3,
    "mind1_v6": 3,
    "curriculum_eligibility": 3,
    "curriculum_session_context": 3,
}

# Per-role max generation tokens. Caps stop generation early on the
# vLLM side, saving latency vs. open-ended output up to model defaults.
# Values are observed-max from a live 3-profile × 5-turn smoke
# (P02/P07/P04) plus ~25% safety buffer so no role truncates. If you
# observe truncation in a future run, raise the cap for that role.
#
# Observed maxes (output tokens, approx chars/4):
#   inference          1454
#   recompute           935
#   response_v6         524
#   miti_judge_v6       363
#   mind1_v6            345
#   persona_update_v6   933
#   session_context     285
def _max_tokens(role: str, default: int) -> int:
    """Per-role max_tokens, overridable via ``HELPE_MAX_TOKENS_<ROLE>``.

    Lets callers (e.g. a Fireworks wrapper using gpt-oss-120b, whose
    ``reasoning_content`` eats budget) bump caps without editing this
    file. Defaults below are unchanged.
    """
    return int(os.environ.get(f"HELPE_MAX_TOKENS_{role.upper()}", default))


MAX_TOKENS_BY_ROLE: Final[dict[str, int]] = {
    # Bumped from 1800 → 2000 (1.3× margin on all-20-problem worst case).
    "inference": _max_tokens("inference", 2000),
    # Bumped from 1200 → 1400 (1.3× margin).
    "recompute": _max_tokens("recompute", 1400),
    "response_v6": _max_tokens("response_v6", 800),
    "miti_judge": _max_tokens("miti_judge", 500),
    # 6 dims × {name+score+1-sentence justification} ≈ 600 tokens; 700
    # is enough headroom. Replaces the old `mind3` cap (2500) which was
    # for an all-sessions-at-once design that consistently truncated.
    "esc_judge": _max_tokens("esc_judge", 700),
    # SIM roles: gpt-oss models (both 20b and 120b) emit
    # `reasoning_content` (chain-of-thought) that COUNTS AGAINST
    # max_tokens, so the cap must cover reasoning + actual content.
    # Earlier 500-token cap caused 75% empty responses for mind1_v6
    # because reasoning consumed the budget before any content emitted
    # (finish_reason: "length", no message.content).
    "mind1_v6": _max_tokens("mind1_v6", 2500),
    # session_context worst-case actual ~280 tokens; 500 has plenty of
    # headroom and avoids paying for unused budget. Was 2000 (7× overkill).
    "session_context": _max_tokens("session_context", 500),
    "persona_update_v6": _max_tokens("persona_update_v6", 1200),
    # v3 combined extract: per-problem summaries + TTM stages can run
    # ~2–4 problems × ~150 tokens each + framing. Bumped from 1500 →
    # 1800 (1.25× → 1.5× margin) so 4-problem sessions don't truncate.
    "v3_extract": _max_tokens("v3_extract", 1800),
    # v4 combined extract: same as v3 + free-form problem_connections
    # (~50–100 tokens per connection × up to a few connections). Same
    # cap as v3_extract; bump if observed truncation appears.
    "v4_extract": _max_tokens("v4_extract", 1800),
    # Curriculum gen — one-time pre-experiment work.
    "curriculum_eligibility": _max_tokens("curriculum_eligibility", 800),
    "curriculum_session_context": _max_tokens("curriculum_session_context", 800),
    # Legacy v1–v5 roles fall through to the default (no cap).
}

# Default ceiling for any role NOT listed in MAX_TOKENS_BY_ROLE. Set
# generously high so legacy paths aren't accidentally truncated; vLLM
# stops naturally at JSON close-brace anyway.
DEFAULT_MAX_TOKENS: Final[int] = 4096
