import json
from pathlib import Path
import networkx as nx
from pyvis.network import Network

NODES_PATH = Path("5_nodes.json")
EDGES_PATH = Path("5_edges.json")
OUT_HTML = "quantum_network.html"

HELIX_COLORS = {
    "Academia": "#4C78A8",
    "Industry": "#F58518",
    "Government": "#54A24B",
    "Civil Society": "#B279A2",
    "Intermediary": "#E45756",
    "Unknown": "#999999",
}

RELATION_COLORS = {
    "networking": "#6B7280",
    "technology_transfer": "#D97706",
    "collaborative_leadership": "#2563EB",
    "substitution": "#DC2626",
    "collaboration_conflict_moderation": "#7C3AED",
}

def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))

def main():
    nodes = load_json(NODES_PATH)
    edges = load_json(EDGES_PATH)

    G = nx.DiGraph()

    for n in nodes:
        key = n.get("canonical_actor_key") or n.get("entity")
        label = n.get("entity", key)
        helix = n.get("helix", "Unknown")
        category = n.get("category", "")
        review = n.get("classification_needs_review") or n.get("needs_review")

        G.add_node(
            key,
            label=label,
            helix=helix,
            category=category,
            color=HELIX_COLORS.get(helix, "#999999"),
            title=(
                f"<b>{label}</b><br>"
                f"Helix: {helix}<br>"
                f"Category: {category}<br>"
                f"Review: {review}"
            ),
        )

    for e in edges:
        source = e.get("source_actor_key") or e.get("source_actor")
        target = e.get("target_actor_key") or e.get("target_actor")
        relation = e.get("relation_label", "unknown")
        confidence = e.get("relation_label_confidence", "")
        functional_space = e.get("functional_space", "")
        phrase = e.get("interaction_phrase", "")
        page = e.get("page", "")

        if source not in G:
            G.add_node(source, label=e.get("source_actor", source), color="#999999")
        if target not in G:
            G.add_node(target, label=e.get("target_actor", target), color="#999999")

        G.add_edge(
            source,
            target,
            label=relation,
            color=RELATION_COLORS.get(relation, "#999999"),
            title=(
                f"<b>{relation}</b><br>"
                f"Confidence: {confidence}<br>"
                f"Functional space: {functional_space}<br>"
                f"Page: {page}<br>"
                f"Phrase: {phrase}"
            ),
        )

    # cdn_resources='remote' makes the HTML load vis-network.js from a CDN
    # rather than expecting a local lib/ folder next to the HTML. This keeps
    # outputs small (one HTML file per run, no JS bundle alongside) and means
    # the HTML works from anywhere on disk -- but it does require an internet
    # connection to view the rendered graph. Switch to 'local' if you need
    # offline-viewable outputs.
    net = Network(
        height="850px", width="100%", directed=True, notebook=False,
        cdn_resources="remote",
    )
    net.from_nx(G)

    for node in net.nodes:
        node["size"] = 18
        node["font"] = {"size": 18}

    for edge in net.edges:
        edge["width"] = 2
        edge["arrows"] = "to"

    net.set_options("""
    {
      "physics": {
        "barnesHut": {
          "gravitationalConstant": -8000,
          "centralGravity": 0.3,
          "springLength": 180,
          "springConstant": 0.04
        },
        "minVelocity": 0.75
      },
      "interaction": {
        "hover": true,
        "navigationButtons": true,
        "keyboard": true
      }
    }
    """)

    net.write_html(OUT_HTML)
    print(f"Wrote {OUT_HTML}")
    print(f"Nodes: {G.number_of_nodes()}")
    print(f"Edges: {G.number_of_edges()}")

if __name__ == "__main__":
    main()