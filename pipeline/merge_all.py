#!/usr/bin/env python3
"""Merge per-document network outputs into one combined graph.

Walks pdf_outputs/*/ and site_outputs/*/website/ for 5_nodes.json + 5_edges.json
pairs, applies document-relative rewrites (e.g. "We" -> "Japan" in japan25.pdf),
deduplicates actors across sources, and writes:

    merged_outputs/
      combined_nodes.json       <- one record per canonical actor
      combined_edges.json       <- all edges, with rewrites applied
      merge_report.json         <- diagnostics: rewrite counts, helix conflicts
      network.html              <- combined pyvis visualisation

Rewrites are configured in merge_rewrites.json next to this script. Edit it
when you spot new patterns (a country's PDF using "we", a site using generic
"government", etc.) and re-run. Use --dry-run to preview the impact of a
rewrite change without writing files.

Usage:
    python3 merge_all.py                          # write merged outputs
    python3 merge_all.py --dry-run                # show actions, don't write
    python3 merge_all.py --no-network             # skip network.html generation
    python3 merge_all.py --rewrites custom.json   # use a non-default rewrite file
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------
# Rewrite map handling
# --------------------------------------------------------------------------

DEFAULT_REWRITE_FILE = "merge_rewrites.json"

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


def apply_rewrite(entity: str, source: str, rewrites: dict) -> tuple[str, str | None]:
    """Apply per-source rewrites to entity. Returns (new_entity, rule_used_or_None)."""
    # Per-source first, then global. First match wins.
    for source_key in [source, "*"]:
        for rule in rewrites.get(source_key, []):
            match = rule.get("match", "")
            replace = rule.get("replace", "")
            if not match:
                continue
            if re.search(match, entity, flags=re.IGNORECASE):
                return replace, f"{source_key}: {match} -> {replace}"
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

    # First pass: apply rewrites in place (on a copy).
    rewritten: list[tuple[dict, str]] = []
    for node, label in all_nodes:
        node = dict(node)
        src = node.get("source_document", "")
        original = node.get("entity", "")
        new_entity, rule_used = apply_rewrite(original, src, rewrites)
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
        "helix_conflicts": helix_conflicts,
    }
    return merged, diagnostics


def merge_edges(
    all_edges: list[tuple[dict, str]],
    rewrites: dict,
    merged_nodes: list[dict],
) -> list[dict]:
    """Apply rewrites to edge actors, then dedupe.

    Also re-points source/target actor keys to the merged canonical keys.
    """
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

    rewritten: list[dict] = []
    for edge, _label in all_edges:
        edge = dict(edge)
        src = edge.get("source_document", "")
        for field in ("source_actor", "target_actor"):
            original = edge.get(field, "")
            new_name, _rule = apply_rewrite(original, src, rewrites)
            edge[field] = new_name
        edge["source_actor_key"] = resolve_key(edge.get("source_actor", ""))
        edge["target_actor_key"] = resolve_key(edge.get("target_actor", ""))
        rewritten.append(edge)

    # Dedup. Same logic as clean_interactions.dedupe_edges.
    seen: set = set()
    deduped: list[dict] = []
    for edge in rewritten:
        s = edge.get("source_actor_key", "")
        t = edge.get("target_actor_key", "")
        pair = tuple(sorted([s, t]))
        key = (
            pair,
            (edge.get("interaction_phrase", "") or "").strip().lower(),
            (edge.get("occurrence_sentence", "") or "").strip().lower(),
            str(edge.get("source_document", "")).strip(),
            str(edge.get("page", "")).strip(),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(edge)

    return deduped


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
        "--no-network", action="store_true",
        help="Skip running network.py at the end.",
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

    merged_nodes, diagnostics = merge_nodes(all_nodes, rewrites)
    merged_edges = merge_edges(all_edges, rewrites, merged_nodes)

    # Load the news manifest (URL -> {date, title, slug}) if it exists, and
    # join article dates onto each node and edge whose source_document matches
    # a news URL. Done at merge time rather than extraction time so the date
    # stays out of the per-doc pipeline; it's metadata about the article, not
    # about the extracted actors/interactions inside it.
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
        # Edges have a single source_document -> single date.
        for e in merged_edges:
            sd = e.get("source_document", "")
            if sd in url_to_date and url_to_date[sd]:
                e["source_date"] = url_to_date[sd]
        # Nodes have source_documents (plural -- merged across articles). Join
        # the union of dates so the viz can filter "actor appears in any
        # article in this range" or "actor's earliest mention is...".
        for n in merged_nodes:
            sds = n.get("source_documents") or []
            dates = sorted({url_to_date[sd] for sd in sds if sd in url_to_date and url_to_date[sd]})
            if dates:
                n["source_dates"] = dates
                n["earliest_date"] = dates[0]
                n["latest_date"] = dates[-1]
        joined_edges = sum(1 for e in merged_edges if "source_date" in e)
        joined_nodes = sum(1 for n in merged_nodes if "source_dates" in n)
        print(f"\nJoined article dates from news manifest: "
              f"{joined_nodes} nodes, {joined_edges} edges tagged.")

    print(f"\nAfter merge: {len(merged_nodes)} unique actors, {len(merged_edges)} unique edges.")

    if diagnostics["rewrites_applied"]:
        print("\nRewrites applied:")
        for rule, n in sorted(diagnostics["rewrites_applied"].items(), key=lambda x: -x[1]):
            print(f"  {n:4d}x  {rule}")
    else:
        print("\nNo rewrites applied.")

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
    (out_dir / "merge_report.json").write_text(
        json.dumps({
            "source_count": len(pairs),
            "sources": [label for _, _, label in pairs],
            "input_node_records": len(all_nodes),
            "input_edge_records": len(all_edges),
            "output_unique_actors": len(merged_nodes),
            "output_unique_edges": len(merged_edges),
            **diagnostics,
        }, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"\nWrote {out_dir / 'combined_nodes.json'}")
    print(f"Wrote {out_dir / 'combined_edges.json'}")
    print(f"Wrote {out_dir / 'merge_report.json'}")

    if args.no_network:
        return

    # network.py expects 5_nodes.json / 5_edges.json in cwd, so symlink (or copy).
    nodes_link = out_dir / "5_nodes.json"
    edges_link = out_dir / "5_edges.json"
    nodes_link.write_text((out_dir / "combined_nodes.json").read_text(encoding="utf-8"), encoding="utf-8")
    edges_link.write_text((out_dir / "combined_edges.json").read_text(encoding="utf-8"), encoding="utf-8")

    network_script = root / "network.py"
    if not network_script.exists():
        print(f"\nSkipping network.html: {network_script} not found.")
        return

    print("\nRunning network.py to render combined graph...")
    subprocess.run([sys.executable, str(network_script)], cwd=out_dir, check=True)
    html = out_dir / "quantum_network.html"
    if html.exists():
        html.rename(out_dir / "network.html")
    print(f"Wrote {out_dir / 'network.html'}")


if __name__ == "__main__":
    main()