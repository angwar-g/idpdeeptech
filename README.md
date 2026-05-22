# Pipeline Cheat Sheet

Two parallel pipelines that extract actor networks from text and render them as an interactive graph: one for PDFs, one for crawled websites. They share the same downstream cleaning, classification, and visualisation steps.

## PDF Pipeline

```
python3 pdf_pipeline.py <pdf_filename>
```

| Flag | Shortcut | Description |
|---|---|---|
| `--skip-actors` | `-s` | Reuse `2_actor_nodes_pdf.json`, jump to interactions. Requires a previous full run. |

**Examples**

```
python3 pdf_pipeline.py china25.pdf
python3 pdf_pipeline.py china25.pdf -s
```

**Input:** drop PDFs into `pdf_input/`
**Output:** `pdf_outputs/<pdf_stem>/`

---

## Site Pipeline

```
python3 site_pipeline.py <url>
```

| Flag | Shortcut | Description |
|---|---|---|
| `--crawl N` | `-c N` | Crawl depth (default `2`). Higher = follows more internal links. |
| `--max-pages N` | | Max pages to crawl (default `50`). Safety ceiling. |
| `--skip-crawl` | | Reuse existing `crawl_output/`. |
| `--skip-actors` | `-s` | Reuse cleaned actors. Implies `--skip-crawl`. |

**Examples**

```
python3 site_pipeline.py https://www.psiquantum.com
python3 site_pipeline.py https://www.psiquantum.com -c 3 --max-pages 80
python3 site_pipeline.py https://www.psiquantum.com --skip-crawl
python3 site_pipeline.py https://www.psiquantum.com -s
```

**Output:** `site_outputs/<domain>/`
Each fresh crawl wipes the site's `crawl_output/` first — no stale files.

---

## What each step writes

| File | Step | LLM call? |
|---|---|---|
| `1_actor_results_pdf.json` | Raw actor extraction | yes |
| `2_actor_nodes_pdf.json` | Cleaned + deduped actors | no |
| `3_interaction_results_pdf.json` | Raw interaction extraction | yes |
| `4_interaction_edges_pdf.json` | Cleaned + deduped edges | no |
| `5_nodes.json` | Actors + triple-helix classification | no |
| `5_edges.json` | Edges + functional-space classification | no |
| `network.html` | Interactive pyvis visualisation | — |

The two LLM steps (1 and 3) are the slow ones. Both save incrementally after each page (PDFs) or each URL (sites), so a mid-run crash keeps prior work on disk. Filenames keep the `_pdf` suffix even in the site pipeline so the shared `clean_*.py`, `helix.py`, and `network.py` scripts work unchanged.

---

## Recovery patterns

| Situation | Command |
|---|---|
| Interactions extraction crashed mid-run on a PDF | `python3 pdf_pipeline.py same.pdf -s` |
| Interactions extraction crashed mid-run on a site | `python3 site_pipeline.py same_url -s` |
| Want to tweak the LLM prompt and re-extract | delete `2_actor_nodes_pdf.json` and rerun without `-s` |
| Want to re-crawl with different depth | rerun without `--skip-crawl` (wipes `crawl_output/`) |
| Want to re-run only the downstream stuff (helix + viz) | run `helix.py` and `network.py` directly in the output folder |
