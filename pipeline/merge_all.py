#!/usr/bin/env python3
"""Merge per-document network outputs into one combined graph.

Walks pdf_outputs/*/, site_outputs/*/, and news_outputs/*/ for 5_nodes.json +
5_edges.json pairs, applies document-relative rewrites (e.g. "We" -> "Japan"
in japan25.pdf), deduplicates actors across sources, collapses edges into
logical edges with occurrence lists, joins news article dates from the news
manifest, and writes:

    pipeline/merged_outputs/
      combined_nodes.json       <- one record per canonical actor
      combined_edges.json       <- one record per logical edge, with
                                   occurrences[] listing every (source, page,
                                   sentence, date) mention
      merge_report.json         <- diagnostics: rewrite counts, helix conflicts

After writing, the two combined_*.json files are also copied to docs/data/
(configurable via --publish-to) so the UI can fetch them when served locally
or via GitHub Pages. The merge_report.json is NOT copied -- it's a
development/audit artifact and doesn't belong on the public site.

The output is consumed by the frontend UI (vis-network) directly. We do not
render an HTML preview here -- the UI is the renderer.

Rewrites are configured in merge_rewrites.json next to this script. Edit it
when you spot new patterns (a country's PDF using "we", a site using generic
"government", etc.) and re-run. Use --dry-run to preview the impact of a
rewrite change without writing files.

Usage:
    python3 merge_all.py                          # write merged outputs + publish
    python3 merge_all.py --dry-run                # show actions, don't write
    python3 merge_all.py --no-publish             # don't copy to docs/data/
    python3 merge_all.py --publish-to <dir>       # copy to a different dir
    python3 merge_all.py --rewrites custom.json   # use a non-default rewrite file
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Rewrite map handling
# --------------------------------------------------------------------------

DEFAULT_REWRITE_FILE = "merge_rewrites.json"


# --------------------------------------------------------------------------
# Hardcoded helix overrides
# --------------------------------------------------------------------------
# Countries and sub-national regions are *definitionally* Government-helix
# actors regardless of how the LLM classified them in any individual source.
# Per-doc confidence-weighted helix resolution gets these wrong because some
# sources describe a country in industry context ("the UK quantum industry")
# and that gets picked up as helix="Industry".
#
# This override is applied AFTER cross-document helix resolution but BEFORE
# the conflict is logged, so the merge_report reflects the corrected helix
# and the override count is auditable.
#
# Add new entries here when you spot countries/regions in your data that
# aren't covered. Names are matched case-insensitively against the canonical
# actor name AFTER merge_rewrites has been applied -- so use the canonical
# form (e.g. "United States", not "USA"; "Germany", not "Deutschland").
COUNTRY_GOVERNMENT_ACTORS: frozenset[str] = frozenset({
    # Europe
    "austria", "belgium", "bulgaria", "croatia", "cyprus", "czech republic",
    "czechia", "denmark", "estonia", "finland", "france", "germany", "greece",
    "hungary", "iceland", "ireland", "italy", "latvia", "lithuania",
    "luxembourg", "malta", "netherlands", "norway", "poland", "portugal",
    "romania", "slovakia", "slovenia", "spain", "sweden", "switzerland",
    "united kingdom", "uk", "great britain", "scotland", "wales",
    "ukraine", "serbia",
    # Asia-Pacific
    "australia", "new zealand", "china", "japan", "south korea", "korea",
    "north korea", "republic of korea", "taiwan", "thailand", "vietnam",
    "philippines", "indonesia", "malaysia", "singapore", "india",
    "bangladesh", "pakistan", "sri lanka", "hong kong",
    # Americas
    "united states", "canada", "mexico", "brazil", "argentina", "chile",
    "colombia", "peru", "venezuela",
    # Middle East / Africa
    "israel", "saudi arabia", "kingdom of saudi arabia", "uae",
    "united arab emirates", "turkey", "iran", "iraq", "qatar", "kuwait",
    "egypt", "south africa", "kenya", "nigeria", "morocco",
    # Russia / Central Asia
    "russia", "kazakhstan",
    # Supranational
    "european union", "asean", "nato",
    # US states and major sub-national regions present in current data
    "california", "illinois", "texas", "massachusetts", "new york",
    "quebec", "ontario", "bavaria", "baden-württemberg",
})


DEFAULT_REWRITES = {
    "_comment": (
        "Per-source actor rewrites applied BEFORE cross-document merging. "
        "Keys are source_document values exactly as they appear in 5_nodes.json. "
        "Each rewrite has a 'match' (case-insensitive regex on the full entity name) "
        "and 'replace' (the new entity name). "
        "Wildcards: use 'source_document' '*' to apply to every source."
    ),
    "rewrites": {
        "japan25.pdf": [
            {"match": "^we$", "replace": "Japan"},
            {"match": "^our country$", "replace": "Japan"},
            {"match": "^(the )?government$", "replace": "Japan Government"},
            {"match": "^(the )?national government$", "replace": "Japan Government"},
        ],
        "china25.pdf": [
            {"match": "^(the )?state council$", "replace": "China State Council"},
        ],
        "*": [
            # Patterns that apply regardless of source document. Add carefully.
        ],
    },
}


def load_rewrites(path: Path) -> dict:
    """Load rewrite map. Create with defaults if missing."""
    if not path.exists():
        path.write_text(
            json.dumps(DEFAULT_REWRITES, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Created default rewrite map at {path}. Edit it as needed and re-run.")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("rewrites", {})


def apply_rewrite(entity: str, source: str, rewrites: dict) -> tuple[str | None, str | None]:
    """Apply per-source rewrites to entity.

    Returns (new_entity, rule_used_or_None) where new_entity is:
      - the rewritten string (possibly with regex capture-group substitution)
      - the original entity if no rule matched
      - None if the actor was matched by a drop rule and should be removed

    Rule schema (in rewrites.json under "rewrites" -> source_document key):

      Standard rewrite:
        { "match": "<regex>", "replace": "<replacement>" }
        Supports capture groups: "match": "^(.+) ltd\\.?$", "replace": "$1"

      Drop unconditionally (actor + its edges removed):
        { "match": "<regex>", "drop": true }

      Drop only if no earlier rule already renamed this actor. Useful for
      generic terms like "Government" -- a doc-specific rule may already
      have rewritten it to "Japan Government", but if not, drop it.
        { "match": "<regex>", "drop_if_unmapped": true }

    Matching order: per-source rules first, then "*" (wildcard) rules. First
    match within either group wins. The "drop_if_unmapped" flag is checked at
    the * stage and only fires if no per-source rule already produced a name
    change.
    """
    # Track whether an earlier (per-source) rule already changed the name.
    already_mapped = False
    current = entity

    for source_key in [source, "*"]:
        for rule in rewrites.get(source_key, []):
            match_re = rule.get("match", "")
            if not match_re:
                continue
            if not re.search(match_re, current, flags=re.IGNORECASE):
                continue

            # Drop rules return None as the new entity.
            if rule.get("drop"):
                return None, f"{source_key}: {match_re} -> [drop]"
            if rule.get("drop_if_unmapped"):
                if already_mapped:
                    # An earlier rule already renamed this actor, so don't drop.
                    continue
                return None, f"{source_key}: {match_re} -> [drop_if_unmapped]"

            # Standard rewrite with regex capture-group substitution.
            # Users write $1, $2, ... (JS-style); convert to Python's \1, \2.
            replace = rule.get("replace", "")
            replace_py = re.sub(r"\$(\d+)", r"\\\1", replace)
            try:
                new_entity = re.sub(
                    match_re, replace_py, current, flags=re.IGNORECASE
                )
            except re.error as exc:
                print(f"WARNING: bad regex in rewrite rule "
                      f"({source_key}: {match_re}): {exc}")
                continue

            # If we're in the per-source pass, remember and continue so that
            # later wildcard rules see the already-renamed value (and can
            # avoid re-dropping it via drop_if_unmapped).
            if source_key != "*":
                current = new_entity
                already_mapped = True
                # Keep applying later rules to the new name. Don't return yet --
                # this is what allows "Government" -> "Japan Government" (per-doc)
                # followed by no-op of the global "drop_if_unmapped" rule.
                # We return at the first WILDCARD match below, or after the loop.
                continue

            return new_entity, f"{source_key}: {match_re} -> {replace}"

    if already_mapped:
        # Per-source rule(s) renamed it but no wildcard rule fired.
        return current, "per-source rewrite chain"
    return entity, None


# --------------------------------------------------------------------------
# Discovery
# --------------------------------------------------------------------------

def find_output_pairs(root: Path) -> list[tuple[Path, Path, str]]:
    """Return list of (nodes_path, edges_path, label) tuples found on disk."""
    pairs: list[tuple[Path, Path, str]] = []

    pdf_root = root / "pdf_outputs"
    if pdf_root.exists():
        for sub in sorted(pdf_root.iterdir()):
            if sub.is_dir():
                n = sub / "5_nodes.json"
                e = sub / "5_edges.json"
                if n.exists() and e.exists():
                    pairs.append((n, e, f"pdf/{sub.name}"))

    site_root = root / "site_outputs"
    if site_root.exists():
        for sub in sorted(site_root.iterdir()):
            if sub.is_dir():
                # Batch layout: site_outputs/<company>/website/
                website = sub / "website"
                if website.exists():
                    n = website / "5_nodes.json"
                    e = website / "5_edges.json"
                    if n.exists() and e.exists():
                        pairs.append((n, e, f"site/{sub.name}/website"))
                # Single-URL layout: site_outputs/<domain>/
                else:
                    n = sub / "5_nodes.json"
                    e = sub / "5_edges.json"
                    if n.exists() and e.exists():
                        pairs.append((n, e, f"site/{sub.name}"))

    # News articles: same layout as site_outputs but a separate top folder.
    news_root = root / "news_outputs"
    if news_root.exists():
        for sub in sorted(news_root.iterdir()):
            if sub.is_dir():
                n = sub / "5_nodes.json"
                e = sub / "5_edges.json"
                if n.exists() and e.exists():
                    pairs.append((n, e, f"news/{sub.name}"))

    return pairs


# --------------------------------------------------------------------------
# Normalisation + merging
# --------------------------------------------------------------------------

def canonical_key(entity: str) -> str:
    """Loose key for cross-document actor matching."""
    text = (entity or "").lower().strip()
    text = re.sub(r"\([^)]*\)", "", text)          # drop parentheticals
    text = re.sub(r"[^a-z0-9]+", " ", text)         # punct -> space
    text = re.sub(r"\b(the|a|an)\b", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def choose_better_actor(a: dict, b: dict) -> dict:
    """Pick the more confident actor record when two sources disagree.

    Preference order:
      1. classification_needs_review = False
      2. helix != 'Unknown'
      3. longer occurrence_sentence (proxy for richer context)
    """
    def score(rec: dict) -> tuple:
        return (
            0 if rec.get("classification_needs_review") else 1,
            0 if rec.get("helix") in (None, "", "Unknown") else 1,
            len(str(rec.get("occurrence_sentence", ""))),
        )
    return a if score(a) >= score(b) else b


def merge_nodes(
    all_nodes: list[tuple[dict, str]],   # (node, source_label)
    rewrites: dict,
) -> tuple[list[dict], dict]:
    """Merge nodes across sources. Returns (merged_nodes, diagnostics)."""
    rewrite_log: Counter = Counter()
    helix_conflicts: list[dict] = []
    helix_overrides_applied: list[dict] = []
    dropped_log: Counter = Counter()  # rule_used -> count of dropped actors

    # First pass: apply rewrites in place (on a copy). Some rewrites are
    # drops (apply_rewrite returns None); those actors are removed entirely
    # and their canonical keys recorded so we can also drop edges touching
    # them later.
    rewritten: list[tuple[dict, str]] = []
    dropped_keys: set[str] = set()
    for node, label in all_nodes:
        node = dict(node)
        src = node.get("source_document", "")
        original = node.get("entity", "")
        new_entity, rule_used = apply_rewrite(original, src, rewrites)

        if new_entity is None:
            # Drop rule fired -- skip this node and remember its key so we
            # can prune edges touching it.
            if rule_used:
                dropped_log[rule_used] += 1
            dropped_keys.add(canonical_key(original))
            continue

        if rule_used and new_entity != original:
            rewrite_log[rule_used] += 1
            node["entity"] = new_entity
            node["_rewritten_from"] = original
            # Also rewrite the canonical_actor_key so edges match cleanly later.
            node["canonical_actor_key"] = canonical_key(new_entity)
        rewritten.append((node, label))

    # Second pass: group by canonical key, merge groups.
    groups: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for node, label in rewritten:
        key = canonical_key(node.get("entity", ""))
        if not key:
            continue
        groups[key].append((node, label))

    merged: list[dict] = []
    for key, members in sorted(groups.items()):
        # Choose the best record as the base.
        base = members[0][0]
        for node, _label in members[1:]:
            base = choose_better_actor(base, node)
        base = dict(base)

        # Merge collections across all members.
        all_aliases: set[str] = set()
        all_pages: set = set()
        all_mentions: list[dict] = []
        source_documents: set[str] = set()
        source_labels: set[str] = set()
        helixes_seen: set[str] = set()

        for node, label in members:
            source_labels.add(label)
            sd = node.get("source_document")
            if sd:
                source_documents.add(sd)
            for a in node.get("aliases", []) or []:
                if a:
                    all_aliases.add(a)
            for p in node.get("pages", []) or []:
                all_pages.add(p)
            # Mentions: keep raw if present, otherwise synthesise from the node itself.
            existing_mentions = node.get("mentions") or []
            if existing_mentions:
                all_mentions.extend(existing_mentions)
            else:
                all_mentions.append({
                    "entity": node.get("entity", ""),
                    "page": node.get("page"),
                    "role_in_text": node.get("role_in_text", ""),
                    "occurrence_sentence": node.get("occurrence_sentence", ""),
                    "source_document": sd or "",
                })
            h = node.get("helix")
            if h:
                helixes_seen.add(h)

        # Apply country/region helix override. Countries and sub-national
        # regions are Government by definition; this corrects the cases where
        # cross-source resolution picked up an "Industry context" mention and
        # mis-classified the country itself. We override BEFORE the conflict
        # is logged so the chosen_helix recorded in merge_report.json shows
        # the corrected value.
        entity_lower = (base.get("entity") or "").lower()
        original_helix_before_override = base.get("helix")
        if entity_lower in COUNTRY_GOVERNMENT_ACTORS:
            if base.get("helix") != "Government":
                base["helix"] = "Government"
                helix_overrides_applied.append({
                    "entity": base.get("entity"),
                    "canonical_actor_key": key,
                    "original_helix": original_helix_before_override,
                    "overridden_to": "Government",
                    "reason": "country_or_region",
                })

        if len(helixes_seen) > 1:
            helix_conflicts.append({
                "entity": base.get("entity"),
                "canonical_actor_key": key,
                "helixes": sorted(helixes_seen),
                "chosen_helix": base.get("helix"),
                "source_documents": sorted(source_documents),
            })

        base["canonical_actor_key"] = key
        base["aliases"] = sorted(all_aliases)
        base["pages"] = sorted(all_pages, key=lambda x: (isinstance(x, str), x))
        base["mentions"] = all_mentions
        base["source_documents"] = sorted(source_documents)
        base["_source_labels"] = sorted(source_labels)
        merged.append(base)

    merged.sort(key=lambda r: canonical_key(r.get("entity", "")))

    diagnostics = {
        "rewrites_applied": dict(rewrite_log),
        "actors_dropped": dict(dropped_log),
        "_dropped_keys": dropped_keys,  # used by merge_edges; not for JSON serialization
        "helix_conflicts": helix_conflicts,
        "helix_overrides_applied": helix_overrides_applied,
    }
    return merged, diagnostics


# Which relation labels are symmetric (undirected) vs directional.
# Symmetric: (A, B, label) and (B, A, label) collapse to one edge.
# Directional: (A, B, label) and (B, A, label) stay as two edges.
SYMMETRIC_RELATIONS = {
    "networking",
    "collaboration_conflict_moderation",
    "no_explicit_relation",
}
DIRECTIONAL_RELATIONS = {
    "technology_transfer",
    "collaborative_leadership",
    "substitution",
}


def merge_edges(
    all_edges: list[tuple[dict, str]],
    rewrites: dict,
    merged_nodes: list[dict],
    dropped_keys: set[str] | None = None,
) -> list[dict]:
    """Collapse all edge mentions into one logical edge per
    (source_actor, target_actor, relation_label, directional) tuple.

    Each logical edge carries an `occurrences` list -- every (source_document,
    page, sentence, date, confidence) where this relation was extracted. The
    UI renders one line per logical edge but can show "this connection
    appears in N sources" via the occurrences list.

    For symmetric relations (networking, etc.), (A, B) and (B, A) collapse to
    the same logical edge; the pair is stored alphabetically for stability.
    For directional relations (technology_transfer, etc.), the direction is
    preserved.

    Also resolves source/target actor names to merged canonical keys.
    Edges touching any actor in `dropped_keys` (returned from merge_nodes) are
    removed -- these are the actors a drop rule removed.
    """
    dropped_keys = dropped_keys or set()
    # Build alias -> canonical_actor_key lookup from merged nodes.
    alias_to_key: dict[str, str] = {}
    for node in merged_nodes:
        key = node["canonical_actor_key"]
        for name in [node.get("entity", "")] + list(node.get("aliases") or []):
            ck = canonical_key(name)
            if ck:
                alias_to_key[ck] = key

    def resolve_key(name: str) -> str:
        ck = canonical_key(name)
        return alias_to_key.get(ck, ck)

    # First pass: apply rewrites and key resolution to every input edge.
    # An edge is dropped if either endpoint was matched by a drop rule, or
    # if either endpoint's canonical key matches an actor that was dropped
    # in the node pass (e.g. "Government" got dropped on a website source).
    rewritten: list[dict] = []
    for edge, _label in all_edges:
        edge = dict(edge)
        src = edge.get("source_document", "")
        endpoint_dropped = False
        for field in ("source_actor", "target_actor"):
            original = edge.get(field, "")
            new_name, _rule = apply_rewrite(original, src, rewrites)
            if new_name is None:
                # Drop rule fired on this endpoint -- discard the whole edge.
                endpoint_dropped = True
                break
            edge[field] = new_name
            # Also defend against the node pass having dropped this key by a
            # rule that the edge's source_document didn't match (e.g. node
            # rewrite came from a different doc). If the *original* canonical
            # key was dropped, drop this edge too.
            if canonical_key(original) in dropped_keys:
                endpoint_dropped = True
                break
        if endpoint_dropped:
            continue

        edge["source_actor_key"] = resolve_key(edge.get("source_actor", ""))
        edge["target_actor_key"] = resolve_key(edge.get("target_actor", ""))

        # Belt-and-braces: also drop if the resolved keys ended up in
        # dropped_keys (covers the case where both an edge endpoint and a
        # node share the same canonical name but the drop rule only matched
        # via the node pass).
        if (edge["source_actor_key"] in dropped_keys or
                edge["target_actor_key"] in dropped_keys):
            continue

        # Drop edges that would self-loop after rewriting (e.g. "We" + "Japan"
        # both rewritten to "Japan").
        if edge["source_actor_key"] and edge["source_actor_key"] == edge["target_actor_key"]:
            continue
        rewritten.append(edge)

    # Second pass: group into logical edges.
    # Key shape:
    #   directional: ("dir", source_key, target_key, label)
    #   symmetric:   ("sym", min(s,t), max(s,t), label)
    # The `directional` boolean is also recorded on the output for the UI.
    grouped: dict[tuple, dict] = {}
    for edge in rewritten:
        s = edge.get("source_actor_key", "")
        t = edge.get("target_actor_key", "")
        if not s or not t:
            continue
        label = edge.get("relation_label", "no_explicit_relation")
        is_directional = label in DIRECTIONAL_RELATIONS

        if is_directional:
            group_key = ("dir", s, t, label)
            canon_s, canon_t = s, t
            canon_src_name = edge.get("source_actor", "")
            canon_tgt_name = edge.get("target_actor", "")
        else:
            # Stable ordering: alphabetical by key. Picks one of the two
            # actor names as the canonical "source" side for display, but
            # the UI should treat both ends equivalently.
            if s <= t:
                canon_s, canon_t = s, t
                canon_src_name = edge.get("source_actor", "")
                canon_tgt_name = edge.get("target_actor", "")
            else:
                canon_s, canon_t = t, s
                canon_src_name = edge.get("target_actor", "")
                canon_tgt_name = edge.get("source_actor", "")
            group_key = ("sym", canon_s, canon_t, label)

        occurrence = {
            "source_document": edge.get("source_document", ""),
            "page": edge.get("page"),
            "interaction_phrase": edge.get("interaction_phrase", ""),
            "occurrence_sentence": edge.get("occurrence_sentence", ""),
            "source_date": edge.get("source_date", ""),
            "relation_label_confidence": edge.get("relation_label_confidence", ""),
            # Preserve the original direction so the UI can show "X did Y to Z"
            # even when the logical edge is stored canonically.
            "source_actor": edge.get("source_actor", ""),
            "target_actor": edge.get("target_actor", ""),
            # Helix + functional space come from the per-doc helix.py step. Each
            # occurrence keeps its own values because the same actor may be
            # classified differently across docs (cross-doc helix conflicts are
            # tracked in merge_report.json). Edge-level aggregates are computed
            # below from these per-occurrence values.
            "source_helix": edge.get("source_helix", ""),
            "target_helix": edge.get("target_helix", ""),
            "helix_pair": edge.get("helix_pair", ""),
            "functional_space": edge.get("functional_space", ""),
            "functional_space_needs_review": edge.get(
                "functional_space_needs_review", False
            ),
            "functional_space_review_reason": edge.get(
                "functional_space_review_reason", ""
            ),
        }

        if group_key in grouped:
            grouped[group_key]["occurrences"].append(occurrence)
        else:
            grouped[group_key] = {
                "source_actor_key": canon_s,
                "target_actor_key": canon_t,
                "source_actor": canon_src_name,
                "target_actor": canon_tgt_name,
                "relation_label": label,
                "directional": is_directional,
                "occurrences": [occurrence],
            }

    # Deduplicate occurrences within each logical edge (same source_document
    # + page + sentence is the same fact picked up twice; collapse).
    for edge in grouped.values():
        seen: set = set()
        unique: list[dict] = []
        for occ in edge["occurrences"]:
            key = (
                str(occ.get("source_document", "")).strip(),
                str(occ.get("page", "")).strip(),
                (occ.get("occurrence_sentence", "") or "").strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(occ)
        edge["occurrences"] = unique

        # Convenience aggregates for the UI / source-filter:
        #  - source_documents: list of unique source docs (in order of first appearance)
        #  - occurrence_count: how many distinct mentions
        #  - first_seen / last_seen: earliest and latest dated occurrence
        srcs_seen: list[str] = []
        seen_set: set = set()
        dates: list[str] = []
        for occ in unique:
            sd = occ.get("source_document", "")
            if sd and sd not in seen_set:
                seen_set.add(sd)
                srcs_seen.append(sd)
            d = occ.get("source_date", "")
            if d:
                dates.append(d)
        edge["source_documents"] = srcs_seen
        edge["occurrence_count"] = len(unique)
        if dates:
            edge["first_seen"] = min(dates)
            edge["last_seen"] = max(dates)

        # Aggregate helix and functional_space across occurrences. Most common
        # value wins, with a tie-break preferring non-empty / non-Unknown. The
        # per-occurrence values are still preserved in `occurrences[]` for
        # anyone who wants source-by-source detail.
        def _most_common_nonempty(values: list[str]) -> str:
            counts: Counter = Counter(v for v in values if v)
            if not counts:
                return ""
            # Prefer non-"Unknown" when counts are equal.
            best = counts.most_common()
            top_count = best[0][1]
            top_tied = [v for v, c in best if c == top_count]
            non_unknown = [v for v in top_tied if v.lower() != "unknown"]
            return (non_unknown or top_tied)[0]

        edge["source_helix"] = _most_common_nonempty(
            [o.get("source_helix", "") for o in unique]
        )
        edge["target_helix"] = _most_common_nonempty(
            [o.get("target_helix", "") for o in unique]
        )
        edge["helix_pair"] = _most_common_nonempty(
            [o.get("helix_pair", "") for o in unique]
        )
        edge["functional_space"] = _most_common_nonempty(
            [o.get("functional_space", "") for o in unique]
        )
        # needs_review is true if ANY occurrence flagged it -- conservative.
        edge["functional_space_needs_review"] = any(
            o.get("functional_space_needs_review") for o in unique
        )

    return sorted(
        grouped.values(),
        key=lambda e: (e["source_actor_key"], e["target_actor_key"], e["relation_label"]),
    )


SYMMETRIC_RELATIONS_DOC = SYMMETRIC_RELATIONS  # exported for tests / introspection
DIRECTIONAL_RELATIONS_DOC = DIRECTIONAL_RELATIONS


def compute_layout(
    merged_nodes: list[dict],
    merged_edges: list[dict],
    scale: float = 3000.0,
) -> dict[str, dict[str, float]]:
    """Lay out the merged graph using networkx spring layout per connected
    component, then arrange components spatially so they don't overlap.

    Returns a {canonical_actor_key: {"x": ..., "y": ...}} map suitable for
    direct attachment to nodes. The frontend (vis-network) uses these as
    static positions when showing the full network.

    Layout philosophy:
      - One spring_layout per connected component (matches the look of the
        old per-component frontend layout the user prefers).
      - Edge weight = log(1 + occurrence_count). Well-attested relations
        pull their endpoints closer.
      - Components arranged in a spiral by size (largest at center).
      - Singletons clustered in a separate ring at the outer edge.
    """
    try:
        import networkx as nx
    except ImportError:
        print("WARNING: networkx not installed; skipping layout computation. "
              "Run: pip install networkx")
        return {}

    import math

    # Build the graph.
    G = nx.Graph()
    node_keys = [n["canonical_actor_key"] for n in merged_nodes if n.get("canonical_actor_key")]
    G.add_nodes_from(node_keys)

    for edge in merged_edges:
        s = edge.get("source_actor_key", "")
        t = edge.get("target_actor_key", "")
        if not s or not t or s == t:
            continue
        weight = math.log(1 + edge.get("occurrence_count", 1))
        if G.has_edge(s, t):
            # Multiple logical edges between same pair (e.g. different relation
            # labels). Sum weights so the spring force adds up.
            G[s][t]["weight"] += weight
        else:
            G.add_edge(s, t, weight=weight)

    # Sort components by size, largest first.
    components = sorted(
        nx.connected_components(G),
        key=len,
        reverse=True,
    )

    positions: dict[str, dict[str, float]] = {}
    connected = [c for c in components if len(c) > 1]
    singletons = [c for c in components if len(c) == 1]

    # Lay out each multi-node component independently, then offset.
    # Use a spiral arrangement: component i goes to angle (i * 2.4) radians
    # at radius proportional to sqrt of accumulated previous-component sizes.
    cumulative_radius = 0.0
    for i, component in enumerate(connected):
        subgraph = G.subgraph(component)
        size = len(component)
        # spring_layout: k controls optimal distance between nodes; larger
        # graphs need larger k so the layout doesn't compress.
        k = 1.0 / math.sqrt(size) if size > 1 else None
        try:
            sub_positions = nx.spring_layout(
                subgraph,
                k=k,
                iterations=80 if size < 100 else 50,
                weight="weight",
                seed=42,  # deterministic across runs
            )
        except Exception as exc:
            print(f"WARNING: layout failed for component {i} (size {size}): {exc}")
            continue

        # Scale to roughly fit the component in a box proportional to its size.
        component_scale = scale * math.sqrt(size) / 8.0
        # Component center: spiral outward by index.
        if i == 0:
            center_x, center_y = 0.0, 0.0
        else:
            angle = i * 2.4
            cumulative_radius += scale * 0.6 * math.sqrt(size) / 8.0
            center_x = cumulative_radius * math.cos(angle)
            center_y = cumulative_radius * math.sin(angle)

        for key, (nx_x, nx_y) in sub_positions.items():
            positions[key] = {
                "x": center_x + nx_x * component_scale,
                "y": center_y + nx_y * component_scale,
            }

    # Singletons: arrange in a grid in the outer ring, well clear of the main
    # component blob.
    if singletons:
        # Determine outer radius based on what we've already placed.
        max_existing = 0.0
        for p in positions.values():
            d = math.hypot(p["x"], p["y"])
            if d > max_existing:
                max_existing = d
        outer_radius = max(max_existing * 1.4, scale * 1.5)

        cols = max(8, int(math.sqrt(len(singletons))))
        spacing = scale * 0.08
        for i, component in enumerate(singletons):
            key = next(iter(component))
            # Place in two columns far left and far right, alternating.
            side = -1 if i % 2 == 0 else 1
            row = i // 2
            x = side * outer_radius + side * (row % cols) * spacing
            y = (row // cols - len(singletons) // (cols * 4)) * spacing
            positions[key] = {"x": x, "y": y}

    return positions


# --------------------------------------------------------------------------
# Publishing (copy combined_*.json to the UI's data directory)
# --------------------------------------------------------------------------

def publish_to_ui(out_dir: Path, publish_dir: Path) -> None:
    """Copy combined_nodes.json and combined_edges.json to the UI data folder.

    Does not copy merge_report.json -- that's a development/audit artifact
    and doesn't belong on the public site.

    Creates publish_dir (and parents) if missing.
    """
    publish_dir.mkdir(parents=True, exist_ok=True)
    for filename in ("combined_nodes.json", "combined_edges.json"):
        src = out_dir / filename
        dst = publish_dir / filename
        if not src.exists():
            print(f"WARNING: {src} missing; skipping publish of this file.")
            continue
        shutil.copy2(src, dst)
        print(f"Published {src.name} -> {dst}")


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "--rewrites", default=DEFAULT_REWRITE_FILE,
        help=f"Path to rewrite map JSON (default: {DEFAULT_REWRITE_FILE} next to this script).",
    )
    parser.add_argument(
        "--out", default="merged_outputs",
        help="Output directory (default: merged_outputs/).",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done; don't write any files.",
    )
    parser.add_argument(
        "--no-publish", action="store_true",
        help="Skip copying combined_*.json to the UI data directory. "
             "Useful when iterating on merge logic without touching deployed data.",
    )
    parser.add_argument(
        "--publish-to", default=None,
        help="Directory to publish combined_*.json into. "
             "Defaults to <repo_root>/docs/data/ (relative to the pipeline/ folder). "
             "Use --no-publish to skip publishing entirely.",
    )
    args = parser.parse_args()

    root = Path(__file__).parent.resolve()
    rewrites_path = root / args.rewrites if not Path(args.rewrites).is_absolute() else Path(args.rewrites)
    rewrites = load_rewrites(rewrites_path)

    pairs = find_output_pairs(root)
    if not pairs:
        sys.exit(
            "No 5_nodes.json / 5_edges.json pairs found under pdf_outputs/, site_outputs/, or news_outputs/.\n"
            "Run the per-document pipelines first."
        )

    print(f"Found {len(pairs)} source(s):")
    for _n, _e, label in pairs:
        print(f"  - {label}")

    all_nodes: list[tuple[dict, str]] = []
    all_edges: list[tuple[dict, str]] = []
    for nodes_path, edges_path, label in pairs:
        try:
            nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
            edges = json.loads(edges_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  WARNING: skipping {label} due to read/parse error: {e}")
            continue
        for n in nodes:
            all_nodes.append((n, label))
        for e in edges:
            all_edges.append((e, label))

    print(f"\nLoaded {len(all_nodes)} node records and {len(all_edges)} edge records.")

    # Load the news manifest (URL -> {date, title, slug}) so we can attach
    # article dates to each raw edge before they're grouped into occurrences.
    # Done before merge_edges so the dates flow into the occurrences list
    # naturally, and we can compute first_seen / last_seen per logical edge.
    news_manifest_path = root / "news_outputs" / "manifest.json"
    if news_manifest_path.exists():
        try:
            news_manifest = json.loads(news_manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"WARNING: could not read {news_manifest_path}: {e}")
            news_manifest = {}
    else:
        news_manifest = {}

    if news_manifest:
        url_to_date = {
            url: meta.get("date", "") for url, meta in news_manifest.items()
        }
        for raw_edge, _label in all_edges:
            sd = raw_edge.get("source_document", "")
            if sd in url_to_date and url_to_date[sd]:
                raw_edge["source_date"] = url_to_date[sd]
    else:
        url_to_date = {}

    merged_nodes, diagnostics = merge_nodes(all_nodes, rewrites)
    merged_edges = merge_edges(
        all_edges, rewrites, merged_nodes,
        dropped_keys=diagnostics.get("_dropped_keys", set()),
    )

    # Inject the same dates onto merged nodes (each node's source_documents
    # gives a list of URLs/PDFs; intersect with the manifest to get dates).
    if url_to_date:
        for n in merged_nodes:
            sds = n.get("source_documents") or []
            dates = sorted({url_to_date[sd] for sd in sds if sd in url_to_date and url_to_date[sd]})
            if dates:
                n["source_dates"] = dates
                n["earliest_date"] = dates[0]
                n["latest_date"] = dates[-1]
        joined_nodes = sum(1 for n in merged_nodes if "source_dates" in n)
        joined_edges = sum(1 for e in merged_edges if "first_seen" in e)
        print(f"\nJoined article dates from news manifest: "
              f"{joined_nodes} nodes, {joined_edges} edges tagged.")

    print(f"\nAfter merge: {len(merged_nodes)} unique actors, {len(merged_edges)} unique logical edges.")

    # Compute node positions via networkx spring layout (per connected
    # component). The frontend uses these as static coordinates for the full
    # network view, replacing the JS-side layout that produced cluttered
    # placements. Filtered subsets fall back to vis-network's live physics.
    print("\nComputing node layout via networkx...")
    layout = compute_layout(merged_nodes, merged_edges)
    if layout:
        for n in merged_nodes:
            pos = layout.get(n.get("canonical_actor_key", ""))
            if pos:
                n["x"] = round(pos["x"], 2)
                n["y"] = round(pos["y"], 2)
        placed = sum(1 for n in merged_nodes if "x" in n)
        print(f"Placed {placed}/{len(merged_nodes)} actors.")

    if diagnostics["rewrites_applied"]:
        print("\nRewrites applied:")
        for rule, n in sorted(diagnostics["rewrites_applied"].items(), key=lambda x: -x[1]):
            print(f"  {n:4d}x  {rule}")
    else:
        print("\nNo rewrites applied.")

    if diagnostics.get("actors_dropped"):
        total_dropped = sum(diagnostics["actors_dropped"].values())
        print(f"\nActors dropped: {total_dropped}")
        for rule, n in sorted(diagnostics["actors_dropped"].items(), key=lambda x: -x[1]):
            print(f"  {n:4d}x  {rule}")

    if diagnostics.get("helix_overrides_applied"):
        overrides = diagnostics["helix_overrides_applied"]
        print(f"\nHelix overrides applied (countries/regions forced to "
              f"Government): {len(overrides)}")
        for o in overrides[:10]:
            print(f"  - {o['entity']}: {o['original_helix']!r} -> "
                  f"{o['overridden_to']!r}")
        if len(overrides) > 10:
            print(f"  ... and {len(overrides) - 10} more (see merge_report.json)")

    if diagnostics["helix_conflicts"]:
        print(f"\nHelix conflicts (same actor classified differently across sources): "
              f"{len(diagnostics['helix_conflicts'])}")
        for c in diagnostics["helix_conflicts"][:10]:
            print(f"  - {c['entity']}: {c['helixes']} -> chose {c['chosen_helix']}")
        if len(diagnostics["helix_conflicts"]) > 10:
            print(f"  ... and {len(diagnostics['helix_conflicts']) - 10} more (see merge_report.json)")

    if args.dry_run:
        print("\n--dry-run: not writing files.")
        return

    out_dir = root / args.out if not Path(args.out).is_absolute() else Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "combined_nodes.json").write_text(
        json.dumps(merged_nodes, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (out_dir / "combined_edges.json").write_text(
        json.dumps(merged_edges, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # Strip the non-serializable internal set from diagnostics before writing.
    report_diagnostics = {k: v for k, v in diagnostics.items() if not k.startswith("_")}

    (out_dir / "merge_report.json").write_text(
        json.dumps({
            "source_count": len(pairs),
            "sources": [label for _, _, label in pairs],
            "input_node_records": len(all_nodes),
            "input_edge_records": len(all_edges),
            "output_unique_actors": len(merged_nodes),
            "output_unique_edges": len(merged_edges),
            **report_diagnostics,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nWrote {out_dir / 'combined_nodes.json'}")
    print(f"Wrote {out_dir / 'combined_edges.json'}")
    print(f"Wrote {out_dir / 'merge_report.json'}")

    # Publish to the UI data folder. Default target is <repo_root>/docs/data/
    # where repo_root is the parent of pipeline/. This is what the UI (both
    # local and GitHub Pages) fetches from.
    if args.no_publish:
        print("\n--no-publish: skipping copy to UI data directory.")
    else:
        if args.publish_to:
            publish_dir = Path(args.publish_to).resolve()
        else:
            repo_root = root.parent
            publish_dir = repo_root / "docs" / "data"
        print(f"\nPublishing to UI data directory: {publish_dir}")
        publish_to_ui(out_dir, publish_dir)

    print("\nDone. Open the frontend UI to explore the merged graph.")


if __name__ == "__main__":
    main()