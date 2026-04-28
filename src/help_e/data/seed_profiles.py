"""§8.1 — Seed 30 profile YAMLs from EmoCare situation paragraphs.

Two subcommands:

  pick     — pure Python. Map EmoCare's 58 problem_types onto our 20-problem
             vocabulary, sample 30 rows balanced across categories, and
             write one YAML per profile into ``help_e/data/profiles/``. No
             Ollama required. Idempotent: rerunning with the same seed
             yields the same selection.

  extract  — requires Ollama. For each YAML without ``initial_graph``,
             run §18.1 extraction on the seed paragraph as "session 0,
             turn 0" and embed the resulting ProblemGraph snapshot.

Usage:
  python -m help_e.data.seed_profiles pick        --count 30 --seed 7
  python -m help_e.data.seed_profiles pick        --dry-run
  python -m help_e.data.seed_profiles extract     --max 30

Defaults assume EmoCare lives at ``data/EmoCare.jsonl`` relative to the
repo root, and profile YAMLs land under ``src/help_e/data/profiles/``.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import random
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator, Optional

import yaml  # type: ignore

from .. import config
from ..graph import ProblemGraph


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EmoCare → 20-vocab mapping
# ---------------------------------------------------------------------------

# Many-to-one. EmoCare types not in this dict are dropped. We intentionally
# exclude crisis topics (self-harm, ptsd, domestic violence, addictive
# behaviors, schizophrenia, bipolar, ocd, sexual-assault, alcohol abuse,
# harassment) — §2 scope is non-crisis.
EMOCARE_TO_VOCAB: dict[str, str] = {
    "academic pressure":                                    "academic_pressure",
    "workplace stress":                                     "work_stress",
    "burnout":                                              "work_stress",
    "chronic stress":                                       "work_stress",
    "sleep problems":                                       "sleep_problems",
    "procrastination":                                      "procrastination",
    "motivation problems":                                  "procrastination",
    "anxiety disorders":                                    "general_anxiety",
    "emotional fluctuations":                               "general_anxiety",
    "self-esteem issues":                                   "low_self_esteem",
    "goal setting issues":                                  "perfectionism",
    "personal growth challenges":                           "perfectionism",
    "school bullying":                                      "social_anxiety",
    "spirituality and faith":                               "loneliness",
    "conflicts or communication problems":                  "conflicts_with_partner",
    "marital problems":                                     "conflicts_with_partner",
    "breakup with partner":                                 "breakup_aftermath",
    "breakups or divorce":                                  "breakup_aftermath",
    "issues with parents":                                  "conflicts_with_parents",
    "confilicts with parents":                              "conflicts_with_parents",  # typo in source
    "issues with children":                                 "conflicts_with_parents",
    "problems with friends":                                "conflicts_with_friends",
    "financial problems":                                   "financial_stress",
    "budget":                                               "financial_stress",
    "debt problems":                                        "financial_stress",
    "limited resource":                                     "financial_stress",
    "job crisis":                                           "career_uncertainty",
    "job loss or career setbacks":                          "career_uncertainty",
    "career development issues":                            "career_uncertainty",
    "relationship with caregiver":                          "caregiver_stress",
    "death of a loved one (or pet)":                        "grief_of_loved_one",
    "grief and loss":                                       "grief_of_loved_one",
    "health problems":                                      "health_anxiety",
    "chronic illness or pain management":                   "health_anxiety",
    "appearance anxiety":                                   "body_image_concerns",
    "eating disorders":                                     "body_image_concerns",
    "life transitions (e.g., retirement, relocation, role change)": "life_transition",
    "culture shock":                                        "life_transition",
    "wedding":                                              "life_transition",
    "newborn baby":                                         "life_transition",
    "pregenance":                                           "life_transition",  # typo in source
    "identity crises":                                      "life_transition",
    "lgbtq+ identity":                                      "life_transition",
}

# Crisis / out-of-scope (excluded outright — §2 non-crisis scope).
EMOCARE_EXCLUDED: set[str] = {
    "ongoing depression", "post-traumatic stress disorder (ptsd)",
    "self-harm behaviors", "alcohol abuse", "addictive behaviors (e.g., drug use, gambling)",
    "internet addiction", "compulsive behaviors", "obsessive-compulsive disorder (ocd)",
    "schizophrenia", "bipolar disorder", "anger management issues", "harassment",
    "healing from sexual assault or domestic violence", "domestic violence",
}

# Keyword shortlists used by the single-category check. Each key is a
# 20-vocab problem; values are case-insensitive words that strongly hint
# at that category. Only used to discourage paragraphs that bleed into
# other vocabulary categories.
VOCAB_KEYWORDS: dict[str, tuple[str, ...]] = {
    "academic_pressure":      ("exam", "grade", "coursework", "school", "professor", "thesis"),
    "work_stress":            ("deadline", "overtime", "workload", "boss", "burnout", "burned out"),
    "sleep_problems":         ("insomnia", "sleep", "can't sleep", "awake at night"),
    "procrastination":        ("procrastinat", "put off", "keep delaying"),
    "general_anxiety":        ("panic", "worry all the time", "anxious constantly"),
    "low_self_esteem":        ("worthless", "not good enough", "self-esteem"),
    "perfectionism":          ("perfectionis", "never good enough"),
    "social_anxiety":         ("socially anxious", "bullied", "shy in groups"),
    "loneliness":             ("lonely", "isolated", "no one to talk"),
    "conflicts_with_partner": ("spouse", "husband", "wife", "partner and i", "marriage"),
    "breakup_aftermath":      ("breakup", "broke up", "ex-", "divorce"),
    "conflicts_with_parents": ("my mom", "my dad", "my parents", "my mother", "my father"),
    "conflicts_with_friends": ("my friend", "my friends", "group of friends"),
    "financial_stress":       ("money", "debt", "rent", "bills", "afford"),
    "career_uncertainty":     ("job search", "career", "laid off", "new job"),
    "caregiver_stress":       ("caregiver", "caring for my", "take care of"),
    "grief_of_loved_one":     ("passed away", "loss of", "grief", "died"),
    "health_anxiety":         ("diagnos", "chronic pain", "my health"),
    "body_image_concerns":    ("my body", "weight", "appearance", "mirror"),
    "life_transition":        ("moving", "relocated", "retirement", "new role", "graduated"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emocare_path() -> Path:
    # repo-relative default; override with HELPE_EMOCARE_PATH.
    import os
    env = os.environ.get("HELPE_EMOCARE_PATH")
    if env:
        return Path(env)
    pkg_root = Path(__file__).resolve().parents[3]  # src/help_e/data -> repo
    return pkg_root / "data" / "EmoCare.jsonl"


def _iter_emocare(path: Path) -> Iterator[dict]:
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def _count_vocab_hits(text: str) -> dict[str, int]:
    t = text.lower()
    hits: dict[str, int] = {}
    for vocab, kws in VOCAB_KEYWORDS.items():
        c = sum(1 for k in kws if k in t)
        if c:
            hits[vocab] = c
    return hits


def _parse_seeker_profile(s: str) -> dict:
    """Crack the free-form ``seeker_profile`` string into a dict of
    whichever fields are present. EmoCare uses multiple separators
    (commas, three-space chunks) so we try both.
    """
    if not s:
        return {}
    # Split on 2+ spaces OR on commas (but not commas inside a field value).
    parts = re.split(r"\s{2,}|,\s+", s.strip())
    parts = [p for p in (x.strip() for x in parts) if p]
    d: dict = {}
    name_candidate: Optional[str] = None
    for p in parts:
        if ":" in p:
            k, _, v = p.partition(":")
            d[k.strip().lower()] = v.strip()
        elif p.isdigit() and 10 <= int(p) <= 99:
            d["age"] = p
        elif name_candidate is None and re.fullmatch(r"[A-Z][a-z]+", p):
            name_candidate = p
    if name_candidate:
        d["name"] = name_candidate
    return d


def _persona_draft(seeker_profile_str: str) -> dict:
    sp = _parse_seeker_profile(seeker_profile_str)
    traits = sp.get("traits/hobbies") or sp.get("personality traits/hobbies") or sp.get("personality") or ""
    # crude split on commas & "and" — just enough to seed Mind-1.
    trait_list = [
        t.strip() for t in re.split(r",|\band\b", traits) if t.strip()
    ][:4]
    return {
        "personality_traits": trait_list,
        "communication_style": "",
        "relevant_history": sp.get("career") or sp.get("academic major") or "",
        "_raw_seeker_profile": seeker_profile_str,
    }


def _default_session_arc(primary_problem: str) -> list[str]:
    return [
        f"session 1: establish {primary_problem}; surface attributes (severity, barriers)",
        "session 2: explore coping / past_attempts; first TTM move if natural",
        "session 3: introduce a second problem if arc allows; progress on primary",
        "session 4: deeper work on primary; secondary contextualized",
    ]


def _is_single_category(paragraph: str, primary_vocab: str) -> bool:
    """Soft single-category filter: the paragraph's primary vocab should
    be the top-scoring one, or no other vocab should score ≥2 hits.
    Returns True on empty hit dict (nothing to disqualify).
    """
    hits = _count_vocab_hits(paragraph)
    if not hits:
        return True
    if primary_vocab not in hits:
        # paragraph doesn't even trigger its own keywords — allow (many
        # situations are abstract). Still counts as "not conflicting".
        return True
    primary_score = hits[primary_vocab]
    for v, c in hits.items():
        if v == primary_vocab:
            continue
        if c >= primary_score and c >= 2:
            return False
    return True


def _candidate_rows(path: Path) -> list[dict]:
    """Rows whose EmoCare type maps to our 20-vocab AND whose situation
    paragraph passes the single-category soft filter.
    """
    kept: list[dict] = []
    for row in _iter_emocare(path):
        etype = row.get("problem_type", "").strip().lower()
        if etype in EMOCARE_EXCLUDED:
            continue
        vocab = EMOCARE_TO_VOCAB.get(etype)
        if vocab is None:
            continue
        situation = (row.get("situation") or "").strip()
        if len(situation) < 40:
            continue
        if not _is_single_category(situation, vocab):
            continue
        kept.append({
            "emocare_id": row.get("_id") or row.get("id") or "",
            "emocare_type": etype,
            "vocab": vocab,
            "situation": situation,
            "seeker_profile": row.get("seeker_profile") or "",
        })
    return kept


def _balanced_sample(
    rows: list[dict], *, count: int, rng: random.Random,
) -> list[dict]:
    """Round-robin across vocab labels so we don't over-represent the
    huge EmoCare categories (academic pressure, job crisis).
    """
    by_vocab: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_vocab[r["vocab"]].append(r)
    for v in by_vocab:
        rng.shuffle(by_vocab[v])

    order = list(by_vocab.keys())
    rng.shuffle(order)
    picked: list[dict] = []
    pools = {v: iter(by_vocab[v]) for v in order}
    for v in itertools.cycle(order):
        if len(picked) >= count:
            break
        try:
            picked.append(next(pools[v]))
        except StopIteration:
            order.remove(v)
            if not order:
                break
    return picked


# ---------------------------------------------------------------------------
# `pick` subcommand
# ---------------------------------------------------------------------------


def cmd_pick(args: argparse.Namespace) -> int:
    emocare_path = _emocare_path()
    if not emocare_path.exists():
        log.error("EmoCare not found at %s — set HELPE_EMOCARE_PATH.", emocare_path)
        return 2
    rows = _candidate_rows(emocare_path)
    log.info("candidate rows (after mapping + single-category filter): %d", len(rows))
    rng = random.Random(args.seed)
    picked = _balanced_sample(rows, count=args.count, rng=rng)
    log.info("picked %d rows across %d vocab buckets",
             len(picked), len({r["vocab"] for r in picked}))

    out_dir = config.PROFILE_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest: list[dict] = []
    for i, row in enumerate(picked, 1):
        profile_id = f"P{i:02d}"
        y = {
            "profile_id": profile_id,
            "source_emocare_id": row["emocare_id"] or None,
            "source_emocare_type": row["emocare_type"],
            "seed_situation_paragraph": row["situation"],
            "primary_problem": row["vocab"],
            "session_arc": _default_session_arc(row["vocab"]),
            "persona_draft": _persona_draft(row["seeker_profile"]),
            # initial_graph is populated by `extract` subcommand.
            "initial_graph": None,
            "blurb": row["situation"][:180],
        }
        path = out_dir / f"{profile_id}.yaml"
        if args.dry_run:
            log.info("[dry-run] would write %s  (vocab=%s)", path, row["vocab"])
        else:
            with path.open("w") as f:
                yaml.safe_dump(y, f, sort_keys=False, allow_unicode=True)
        manifest.append({
            "profile_id": profile_id, "vocab": row["vocab"],
            "emocare_type": row["emocare_type"],
        })

    if not args.dry_run:
        with (out_dir / "_manifest.json").open("w") as f:
            json.dump(manifest, f, indent=2)
        log.info("wrote %d YAMLs + _manifest.json under %s", len(picked), out_dir)
    return 0


# ---------------------------------------------------------------------------
# `extract` subcommand
# ---------------------------------------------------------------------------


def cmd_extract(args: argparse.Namespace) -> int:
    """For each YAML without initial_graph, run extraction on the seed
    paragraph at (session=0, turn=0) and embed the graph snapshot.

    This call requires the Ollama endpoint to be live. On failure, the
    YAML is left untouched; the session_driver can still seed a
    precontemplation-only graph from `primary_problem` on first run.
    """
    from ..graph_update import apply_turn
    from ..llm_client import CallContext, LLMStructuredError, get_client

    client = get_client()
    out_dir = config.PROFILE_DIR
    yamls = sorted(out_dir.glob("P*.yaml"))
    processed = 0
    skipped = 0
    failed = 0
    for path in yamls:
        with path.open() as f:
            y = yaml.safe_load(f)
        if y.get("initial_graph") and not args.force:
            skipped += 1
            continue
        if args.max is not None and processed >= args.max:
            break
        graph = ProblemGraph()
        graph.set_cursor(0, 0)
        try:
            apply_turn(
                graph=graph, client=client,
                profile_id=y["profile_id"], system="seed",
                session_id=0, turn_id=0,
                user_message=y["seed_situation_paragraph"],
                recent_turns=[],
                previous_main_problem=y["primary_problem"],
                last_n_turns=0,
            )
        except LLMStructuredError as e:
            log.warning("extraction failed for %s: %s", y["profile_id"], e)
            failed += 1
            continue
        y["initial_graph"] = graph.to_dict()
        with path.open("w") as f:
            yaml.safe_dump(y, f, sort_keys=False, allow_unicode=True)
        processed += 1
        log.info("seeded graph for %s", y["profile_id"])
    log.info("seeded %d / skipped %d / failed %d", processed, skipped, failed)
    return 0 if failed == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="help_e.data.seed_profiles")
    p.add_argument("--log-level", default="INFO",
                   choices=("DEBUG", "INFO", "WARNING", "ERROR"))
    sub = p.add_subparsers(dest="cmd", required=True)

    pick = sub.add_parser("pick", help="write YAML stubs from EmoCare")
    pick.add_argument("--count", type=int, default=30)
    pick.add_argument("--seed", type=int, default=7)
    pick.add_argument("--dry-run", action="store_true")
    pick.set_defaults(fn=cmd_pick)

    extract = sub.add_parser("extract", help="populate initial_graph via Ollama extraction")
    extract.add_argument("--max", type=int, default=None,
                         help="stop after processing N files (default: all)")
    extract.add_argument("--force", action="store_true",
                         help="re-run extraction even if initial_graph exists")
    extract.set_defaults(fn=cmd_extract)

    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
