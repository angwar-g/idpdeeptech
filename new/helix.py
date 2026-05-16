#!/usr/bin/env python3
"""
classify_triple_helix.py

Post-process IDP PDF actor and interaction JSON files.

Input:
  - 2_actor_nodes_pdf.json
  - 4_interaction_edges_pdf.json

Output:
  - 5_nodes.json       actors enriched with helix, sphere, r_and_d
  - 5_edges.json edges enriched with source/target helix and functional_space

Usage:
  python classify_triple_helix.py \
    --actors 2_actor_nodes_pdf.json \
    --interactions 4_interaction_edges_pdf.json \
    --out-actors 5_nodes.json \
    --out-interactions 5_edges.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------
# 1) Actor taxonomy rules
# ---------------------------------------------------------------------

# Category names are normalized to lowercase before lookup.
CATEGORY_RULES: Dict[str, Dict[str, str]] = {
    # Single-sphere institutional actors
    "universities": {
        "helix": "Academia",
        "sphere": "Single",
        "r_and_d": "R&D",
    },
    "research institutes": {
        "helix": "Academia",
        "sphere": "Single",
        "r_and_d": "R&D",
    },
    "vocational training institutions": {
        "helix": "Academia",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "small and medium-sized enterprises": {
        "helix": "Industry",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "small and medium-sized enterprises (smes)": {
        "helix": "Industry",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "smes": {
        "helix": "Industry",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "large enterprises": {
        "helix": "Industry",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "corporate labs": {
        "helix": "Industry",
        "sphere": "Single",
        "r_and_d": "R&D",
    },
    "supranational government institutions": {
        "helix": "Government",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "national government institutions": {
        "helix": "Government",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "sub-national government institutions": {
        "helix": "Government",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "media and cultural institutions": {
        "helix": "Civil Society",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "user communities": {
        "helix": "Civil Society",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },
    "non-governmental and non-profit organizations": {
        "helix": "Civil Society",
        "sphere": "Single",
        "r_and_d": "Assessed",
    },

    # Multi-sphere actors / intermediaries
    "joint research centers": {
        "helix": "Intermediary",
        "sphere": "Multi",
        "r_and_d": "R&D",
    },
    "business support institutions": {
        "helix": "Intermediary",
        "sphere": "Multi",
        "r_and_d": "Assessed",
    },
    "financial support institutions": {
        "helix": "Intermediary",
        "sphere": "Multi",
        "r_and_d": "Assessed",
    },

    # Individual innovation roles
    "entrepreneurial scientist": {
        "helix": "Intermediary",
        "sphere": "Multi",
        "r_and_d": "R&D",
    },
    "innovation organizer": {
        "helix": "Intermediary",
        "sphere": "Multi",
        "r_and_d": "Non-R&D",
    },
}

# Your current extractor often emits "individual". This cannot be mapped
# cleanly without knowing the person's innovation role, so keep it reviewable.
DEFAULT_UNKNOWN_RULE = {
    "helix": "Unknown",
    "sphere": "Unknown",
    "r_and_d": "Assessed",
}


# Optional heuristic fallback for common categories missed by the LLM.
# These only apply when category is unknown/missing; they should not override
# an explicit category.
NAME_HEURISTICS: List[Tuple[re.Pattern[str], Dict[str, str], str]] = [
    (
        re.compile(r"\b(university|college|school of|academy of sciences)\b", re.I),
        {"helix": "Academia", "sphere": "Single", "r_and_d": "R&D"},
        "name_heuristic_academia",
    ),
    (
        re.compile(r"\b(institute|laboratory|lab|research center|research centre)\b", re.I),
        {"helix": "Academia", "sphere": "Single", "r_and_d": "R&D"},
        "name_heuristic_research_institute",
    ),
    (
        re.compile(r"\b(ministry|commission|council|government|municipal|state)\b", re.I),
        {"helix": "Government", "sphere": "Single", "r_and_d": "Assessed"},
        "name_heuristic_government",
    ),
    (
        re.compile(r"\b(co\.?|company|corp\.?|corporation|ltd\.?|group|cloud|telecom|technology)\b", re.I),
        {"helix": "Industry", "sphere": "Single", "r_and_d": "Assessed"},
        "name_heuristic_industry",
    ),
    (
        re.compile(r"\b(fund|venture|incubator|accelerator|innovation center|innovation centre)\b", re.I),
        {"helix": "Intermediary", "sphere": "Multi", "r_and_d": "Assessed"},
        "name_heuristic_intermediary",
    ),
    (
        re.compile(r"\b(nonprofit|non-profit|ngo|foundation|association|society|media|museum)\b", re.I),
        {"helix": "Civil Society", "sphere": "Single", "r_and_d": "Assessed"},
        "name_heuristic_civil_society",
    ),
]


# ---------------------------------------------------------------------
# 2) Edge functional-space rules
# ---------------------------------------------------------------------

def functional_space_for_pair(source_helix: str, target_helix: str) -> str:
    """Return functional space from the unordered helix pair."""
    pair = frozenset([source_helix, target_helix])

    if "Unknown" in pair:
        return "Unknown"

    # Any pair involving Civil Society is Public.
    if "Civil Society" in pair:
        return "Public"

    # Same-helix interactions are not defined in the provided cross-helix rules.
    if len(pair) == 1:
        return "Intra-helix / Not classified"

    if pair == frozenset(["Academia", "Government"]):
        return "Knowledge"

    if pair in {
        frozenset(["Academia", "Industry"]),
        frozenset(["Academia", "Intermediary"]),
        frozenset(["Industry", "Intermediary"]),
    }:
        return "Innovation"

    if pair in {
        frozenset(["Government", "Industry"]),
        frozenset(["Government", "Intermediary"]),
    }:
        return "Consensus"

    return "Unknown"


# ---------------------------------------------------------------------
# 3) Helpers
# ---------------------------------------------------------------------

def norm_text(value: Any) -> str:
    return str(value or "").strip()


def norm_category(value: Any) -> str:
    return norm_text(value).lower().replace("&", "and")


def norm_key(value: Any) -> str:
    """Normalize actor names/aliases enough to match edges to nodes."""
    text = norm_text(value).lower()
    text = re.sub(r"\([^)]*\)", "", text)       # remove parenthetical acronym
    text = text.replace("'s", "s")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_actor(actor: Dict[str, Any], use_name_heuristics: bool = True) -> Dict[str, Any]:
    out = dict(actor)
    category = norm_category(out.get("category"))
    rule_source = "category_rule"

    rule = CATEGORY_RULES.get(category)

    # Handle singular/plural drift and minor spelling variants.
    if rule is None:
        category_singular = category.rstrip("s")
        for known_category, known_rule in CATEGORY_RULES.items():
            if known_category.rstrip("s") == category_singular:
                rule = known_rule
                rule_source = "category_rule_fuzzy_plural"
                break

    # If no category rule exists, optionally use cautious name heuristics.
    if rule is None and use_name_heuristics:
        haystack = " ".join(
            [
                norm_text(out.get("entity")),
                norm_text(out.get("role_in_text")),
                norm_text(out.get("occurrence_sentence")),
            ]
        )
        for pattern, heuristic_rule, heuristic_source in NAME_HEURISTICS:
            if pattern.search(haystack):
                rule = heuristic_rule
                rule_source = heuristic_source
                break

    if rule is None:
        rule = DEFAULT_UNKNOWN_RULE
        rule_source = "unknown_category"

    out.update(rule)
    out["classification_rule_source"] = rule_source

    # Keep review signals explicit.
    review_reasons = []
    if out.get("needs_review"):
        review_reasons.append(norm_text(out.get("review_reason")) or "preexisting_needs_review")
    if rule_source == "unknown_category":
        review_reasons.append(f"unmapped_category:{category or 'missing'}")
    if norm_category(out.get("category")) == "individual":
        review_reasons.append("individual_requires_role_classification")

    out["classification_needs_review"] = bool(review_reasons)
    out["classification_review_reason"] = "; ".join(dict.fromkeys(review_reasons))

    return out


def build_actor_lookup(actors: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map canonical names and aliases to actor records."""
    lookup: Dict[str, Dict[str, Any]] = {}
    for actor in actors:
        candidates = [
            actor.get("entity"),
            actor.get("canonical_actor_key"),
            *(actor.get("aliases") or []),
        ]
        for candidate in candidates:
            key = norm_key(candidate)
            if key:
                lookup[key] = actor
    return lookup


def find_actor(name: str, lookup: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    key = norm_key(name)
    if key in lookup:
        return lookup[key]

    # Fallback: edge actor may omit/keep suffixes differently.
    for k, actor in lookup.items():
        if key and (key == k or key in k or k in key):
            return actor

    return None


def classify_edge(edge: Dict[str, Any], lookup: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out = dict(edge)

    source_actor = find_actor(out.get("source_actor", ""), lookup)
    target_actor = find_actor(out.get("target_actor", ""), lookup)

    source_helix = source_actor.get("helix") if source_actor else "Unknown"
    target_helix = target_actor.get("helix") if target_actor else "Unknown"

    out["source_actor_key"] = source_actor.get("canonical_actor_key") if source_actor else None
    out["target_actor_key"] = target_actor.get("canonical_actor_key") if target_actor else None
    out["source_helix"] = source_helix
    out["target_helix"] = target_helix
    out["helix_pair"] = f"{source_helix}–{target_helix}"
    out["functional_space"] = functional_space_for_pair(source_helix, target_helix) # type: ignore

    review_reasons = []
    if source_actor is None:
        review_reasons.append("source_actor_not_found_in_actor_nodes")
    elif source_actor.get("classification_needs_review"):
        review_reasons.append("source_actor_classification_needs_review")

    if target_actor is None:
        review_reasons.append("target_actor_not_found_in_actor_nodes")
    elif target_actor.get("classification_needs_review"):
        review_reasons.append("target_actor_classification_needs_review")

    if out["functional_space"] in {"Unknown", "Intra-helix / Not classified"}:
        review_reasons.append(f"functional_space:{out['functional_space']}")

    out["functional_space_needs_review"] = bool(review_reasons)
    out["functional_space_review_reason"] = "; ".join(dict.fromkeys(review_reasons))

    return out


def load_json(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError(f"{path} must contain a JSON list.")
    return data


def write_json(path: Path, data: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--actors", default="2_actor_nodes_pdf.json")
    parser.add_argument("--interactions", default="4_interaction_edges_pdf.json")
    parser.add_argument("--out-actors", default="5_nodes.json")
    parser.add_argument("--out-interactions", default="5_edges.json")
    parser.add_argument(
        "--no-name-heuristics",
        action="store_true",
        help="Only classify from explicit category labels; do not use actor-name fallback heuristics.",
    )
    args = parser.parse_args()

    actors_in = load_json(Path(args.actors))
    interactions_in = load_json(Path(args.interactions))

    actors_out = [
        classify_actor(actor, use_name_heuristics=not args.no_name_heuristics)
        for actor in actors_in
    ]

    lookup = build_actor_lookup(actors_out)
    interactions_out = [classify_edge(edge, lookup) for edge in interactions_in]

    write_json(Path(args.out_actors), actors_out)
    write_json(Path(args.out_interactions), interactions_out)

    actor_counts: Dict[str, int] = {}
    for actor in actors_out:
        actor_counts[actor["helix"]] = actor_counts.get(actor["helix"], 0) + 1

    edge_counts: Dict[str, int] = {}
    for edge in interactions_out:
        edge_counts[edge["functional_space"]] = edge_counts.get(edge["functional_space"], 0) + 1

    print(f"Wrote {args.out_actors} ({len(actors_out)} actors)")
    print(f"Wrote {args.out_interactions} ({len(interactions_out)} interactions)")
    print("Actor helix counts:", dict(sorted(actor_counts.items())))
    print("Functional-space counts:", dict(sorted(edge_counts.items())))


if __name__ == "__main__":
    main()
