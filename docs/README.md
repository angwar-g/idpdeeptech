# HelixNet - Network Explorer UI

Browser-based viewer for the merged ecosystem graph. Reads `combined_nodes.json` and `combined_edges.json` from `pipeline/merged_outputs/` and renders an interactive network with filters for source, actor, and year.

## Run it locally

The UI is plain HTML + JS, no build step. But a browser will refuse to `fetch()` JSON from `file://` URLs, so you need a local HTTP server.

```bash
# From the repo root
cd /path/to/idpdeeptech
python3 -m http.server 8000
```

Then open in a browser: <http://localhost:8000/graph_ui/index.html>

That's it. The page fetches the merged graph from `../pipeline/merged_outputs/` and renders it.

## Expected directory layout

The `app.js` fetch path assumes:

```
idpdeeptech/
├── pipeline/
│   └── merged_outputs/
│       ├── combined_nodes.json
│       └── combined_edges.json
└── graph_ui/
    ├── index.html
    ├── app.js
    └── style.css
```

## Before you can open the UI

You need merged graph data on disk. From `pipeline/`:

```bash
python3 merge_all.py
```

This writes `combined_nodes.json` and `combined_edges.json` into `pipeline/merged_outputs/`. If you haven't run `merge_all.py` yet, or you've deleted the merge folder, the UI will load with an error in the details panel telling you it couldn't fetch the data.

## What the UI does

- **Full network** loads on page open, **excluding isolated actors** (singleton nodes with no edges). Only the connected portions of the graph is shown by default to keep the view readable.
- **Filter chips** in the sidebar narrow the view by source, actor, or year. Each is searchable. "Select all" / "Select matches" shortcut at the top of each dropdown.
- **Source filter groups by hostname.** Multiple pages of the same website (`https://www.psiquantum.com/about`, `https://psiquantum.com/research`) appear as one `psiquantum.com` entry. PDFs appear individually under their filename.
- **Year filter** uses real article dates from news (`source_date`) plus filename-year extraction for PDFs as fallback.
- **Actor filter is source-aware.** Pick a source first and the actor dropdown re-populates to only actors that appear in those sources.
- **Selecting an actor expands to its connected component.** Filtering by actor shows the actor *and* every other actor reachable from it through the filtered edges, useful for exploring an ego-network plus everyone in its neighborhood.
- **Click a node** to see its helix, sphere, R&D classification, category, source documents, and date range.
- **Click an edge** to see the actors, relation label, directional/symmetric flag, first/last seen dates, and source documents.
- **Edges**:
  - Arrow only when the relation is directional (`technology_transfer`, `collaborative_leadership`, `substitution`).
  - Plain line when symmetric (`networking`, `collaboration_conflict_moderation`).
  - Thickness scales with `occurrence_count`, better-attested relations are visually heavier.
- **Helix legend in the topbar** shows live counts per helix type (Government / Industry / Academia / Intermediary / Civil Society / Unknown) for the currently visible nodes.

## Filter behavior

Filters are AND-ed across types (source × actor × year). Within a type they're OR-ed (any selected source matches).

A short "Preparing graph..." overlay appears between filter changes and the re-render to debounce rapid clicks.

When you filter, the network switches to live physics for the filtered subset (up to ~1500 nodes). Above that threshold, the static fallback layout is used to keep things responsive.

## Layout

The UI computes node positions in JavaScript (per-connected-component force layout, with singletons placed in side columns). This runs every time the full network is rendered.

`merge_all.py` also computes node positions via NetworkX and writes them to `combined_nodes.json` as `x`/`y` fields, but the current UI ignores those and uses its own layout. If we want deterministic placement across reloads, switching to the precomputed coordinates is a small change in `app.js` - see the `getStaticGraphPositions` function. (Currently kept this way because the JS-side layout reacts better to filtered subsets where coordinates would otherwise need recomputation.)

## Editing the UI

Three files, plain JS/HTML/CSS. No bundler, no build step. Edit and refresh.

- `index.html` - sidebar layout, dropdown shells, topbar with helix legend, network container.
- `app.js` - data fetch, filter logic, vis-network rendering, layout.
- `style.css` - theme. Dark blue palette with `--bg-deep`, `--cyan`, `--blue` variables at the top.

The vis-network library is loaded from a CDN (`unpkg.com/vis-network`). If you're working offline, vendor it locally and update the `<script>` tag in `index.html`.

## Common issues

**Page loads but no graph.** Open the browser dev console (F12). If you see `Failed to load graph data: 404`, the fetch path is wrong - either the dev server isn't rooted at the parent directory, or your folder isn't named `graph_ui`. Easiest fix: confirm the URL is `http://localhost:8000/graph_ui/index.html` and that `http://localhost:8000/pipeline/merged_outputs/combined_nodes.json` returns JSON.

**`fetch() failed: file://` error.** You're opening `index.html` by double-clicking instead of via the local server. Browsers block local file fetches for security. Use the `python3 -m http.server` command above.

**The network looks small / "where are all the singleton nodes?"** They're hidden by default, only the connected part of the graph is shown. To bring a specific singleton in, search for it in the actor filter. If it has no edges in the merged data, it'll show up as a lone node when selected.

**Network is too cluttered / hard to read.** That's the default look on a full ~5000 node graph. Use the filters to narrow down: pick 2-3 sources, or one actor and its neighborhood. The filtered view uses live physics so it looks much cleaner.

**Reset button does nothing visible.** It clears all filters and re-renders the connected graph. If the connected graph was already showing, the result looks unchanged. Check the node/edge counts in the sidebar metric cards.

## Data shape (for reference)

If you want to query the merged data outside the UI, the JSON shapes are:

**`combined_nodes.json`** - one record per canonical actor:
```json
{
  "canonical_actor_key": "ionq",
  "entity": "IonQ",
  "aliases": ["IonQ", "IonQ Inc.", "IonQ, Inc."],
  "helix": "Industry",
  "sphere": "...",
  "r_and_d": "R&D",
  "category": "...",
  "source_documents": ["https://ionq.com/", "japan25.pdf", "..."],
  "x": 1234.5,                       /* NetworkX layout; UI currently ignores */
  "y": -678.9,
  "earliest_date": "2019-12-03",     /* if news sources contributed */
  "latest_date": "2024-03-15",
  "source_dates": ["2019-12-03", "2024-03-15", "..."]
}
```

**`combined_edges.json`** - one record per logical edge with all mentions:
```json
{
  "source_actor_key": "ionq",
  "target_actor_key": "aws",
  "source_actor": "IonQ",
  "target_actor": "AWS",
  "relation_label": "networking",
  "directional": false,
  "occurrence_count": 3,
  "source_documents": ["...", "...", "..."],
  "first_seen": "2019-12-03",
  "last_seen": "2024-03-15",
  "source_helix": "Industry",
  "target_helix": "Industry",
  "helix_pair": "Industry–Industry",
  "functional_space": "Knowledge",
  "occurrences": [
    {
      "source_document": "...",
      "page": 1,
      "source_date": "2019-12-03",
      "interaction_phrase": "available via AWS",
      "occurrence_sentence": "...",
      "relation_label_confidence": "high",
      "source_actor": "IonQ",
      "target_actor": "AWS",
      "source_helix": "Industry",
      "target_helix": "Industry",
      "helix_pair": "Industry–Industry",
      "functional_space": "Knowledge",
      "functional_space_needs_review": false
    }
  ]
}
```

## See also

- `pipeline/README.md` - how to run the data pipeline that produces the merged JSON.
- `pipeline/merge_all.py` - the script that produces `combined_*.json` bearing in mind below.
- `pipeline/merge_rewrites.json` - actor-name canonicalization config that's applied at merge time.