"""ProfileSpec + RunConfig — lean dataclasses with no legacy deps.

Extracted from `session_driver.py` (now archived) so the v6 driver
stack can load these without pulling in the entire pre-redesign
v1–v5 module graph (graph.py, mi_selector.py, instruction_response.py,
simulator/mind1.py, simulator/mind3.py, prompts/extraction.py, ...).

The original `ProfileSpec` had a `to_mind1_persona()` method tied to
the legacy `simulator.mind1.Mind1Persona`; that method is dropped here
because the v6 path uses `simulator.session_context.SimulatorProfile`
(constructed by `session_driver_v6._to_simulator_profile`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import config


@dataclass
class ProfileSpec:
    """Mirror of the YAML files produced by §8.1 profile seeding.

    Fields preserved from the legacy ProfileSpec for back-compat with
    existing profile YAMLs. Some are unused by the v6 path:
    - `seed_situation_paragraph`, `primary_problem`, `session_arc`,
      `initial_graph`, `blurb` were used by the v1–v5 simulator stack.
      The v6 simulator (`mind1_v6`) builds its own `SimulatorProfile`
      from `persona_draft` only, so these fields linger only as
      metadata for old YAMLs.
    """

    profile_id: str
    source_emocare_id: Optional[str]
    seed_situation_paragraph: str
    primary_problem: str
    session_arc: list[str]
    persona_draft: dict
    initial_graph: Optional[dict] = None
    blurb: str = ""

    @classmethod
    def from_yaml(cls, path: Path) -> "ProfileSpec":
        with path.open() as f:
            d = yaml.safe_load(f)
        arc = d.get("session_arc") or []
        if isinstance(arc, list) and arc and isinstance(arc[0], dict):
            # YAML schema allows list-of-dicts like ``- session 1: ...``.
            flat = []
            for item in arc:
                flat.extend(f"{k}: {v}" for k, v in item.items())
            arc = flat
        return cls(
            profile_id=d["profile_id"],
            source_emocare_id=d.get("source_emocare_id"),
            seed_situation_paragraph=d.get("seed_situation_paragraph", ""),
            primary_problem=d.get("primary_problem", ""),
            session_arc=arc,
            persona_draft=d.get("persona_draft", {}),
            initial_graph=d.get("initial_graph"),
            blurb=d.get("blurb", d.get("seed_situation_paragraph", "")),
        )


@dataclass
class RunConfig:
    sessions_per_profile: int = 4
    turns_per_session: int = 10
    last_n_turns: int = config.LAST_N_TURNS
    run_judge_inline: bool = False  # legacy hook; v6 judges fire per-session


def load_profile(profile_id: str) -> ProfileSpec:
    return ProfileSpec.from_yaml(config.PROFILE_DIR / f"{profile_id}.yaml")


def list_profiles() -> list[str]:
    if not config.PROFILE_DIR.exists():
        return []
    return sorted(p.stem for p in config.PROFILE_DIR.glob("*.yaml"))


# Self-test (no LLM, no IO except YAML round-trip)
def _self_test() -> None:
    # Construction with required fields.
    spec = ProfileSpec(
        profile_id="T1",
        source_emocare_id=None,
        seed_situation_paragraph="A test seed.",
        primary_problem="academic_pressure",
        session_arc=["s1: hard week"],
        persona_draft={"personality_traits": ["reflective"]},
    )
    assert spec.profile_id == "T1"
    assert spec.blurb == ""

    # RunConfig defaults.
    rc = RunConfig()
    assert rc.sessions_per_profile == 4
    assert rc.turns_per_session == 10

    # YAML round-trip via tempfile.
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump({
            "profile_id": "P_TEST",
            "source_emocare_id": "ec-1",
            "seed_situation_paragraph": "Test seed.",
            "primary_problem": "work_stress",
            "session_arc": [{"session_1": "first session"}],
            "persona_draft": {"communication_style": "guarded"},
        }, f)
        path = Path(f.name)
    try:
        loaded = ProfileSpec.from_yaml(path)
        assert loaded.profile_id == "P_TEST"
        assert loaded.session_arc == ["session_1: first session"]
        assert loaded.persona_draft["communication_style"] == "guarded"
    finally:
        path.unlink()

    # YAML missing optional fields → defaults.
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump({"profile_id": "P_MIN"}, f)
        path = Path(f.name)
    try:
        loaded = ProfileSpec.from_yaml(path)
        assert loaded.profile_id == "P_MIN"
        assert loaded.session_arc == []
        assert loaded.persona_draft == {}
    finally:
        path.unlink()

    print("profile_spec self-test PASSED")


if __name__ == "__main__":
    _self_test()
