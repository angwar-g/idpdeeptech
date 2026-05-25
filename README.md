# Pipeline Cheat Sheet

Three entry points that share the same downstream cleaning, classification, and visualisation steps: one for PDFs, one for a single website, one for a batch of websites from a JSON config.

## PDF Pipeline

```
python3 pdf_pipeline.py <pdf_filename>
```

| Flag | Shortcut | Description |
|---|---|---|
| `--skip-actors` | `-s` | Skip the actor LLM step. Re-runs `clean_actors` and continues normally. Requires `1_actor_results.json`. |
| `--skip-interactions` | `-i` | Skip everything through the interactions LLM. Only re-runs `clean_interactions` + helix + viz. Requires `1_actor_results.json`, `2_actor_nodes.json`, and `3_interaction_results.json`. Implies `--skip-actors`. |

**Examples**

```
python3 pdf_pipeline.py china25.pdf
python3 pdf_pipeline.py china25.pdf -s
python3 pdf_pipeline.py china25.pdf -i
```

**Input:** drop PDFs into `pdf_input/`
**Output:** `pdf_outputs/<pdf_stem>/`

---

## Site Pipeline (single URL)

```
python3 site_pipeline.py <url>
```

| Flag | Shortcut | Description |
|---|---|---|
| `--crawl N` | `-c N` | Crawl depth (default `2`). Higher = follows more internal links. |
| `--max-pages N` | | Max pages to crawl (default `10`). Safety ceiling. |
| `--skip-crawl` | | Reuse existing `crawl_output/`. |
| `--skip-actors` | `-s` | Skip the actor LLM step. Re-runs `clean_actors` and continues normally. Requires `1_actor_results.json`. Implies `--skip-crawl`. |
| `--skip-interactions` | `-i` | Skip everything through the interactions LLM. Only re-runs `clean_interactions` + helix + viz. Requires `1_actor_results.json`, `2_actor_nodes.json`, and `3_interaction_results.json`. Implies `--skip-actors` (and `--skip-crawl`). |
| `--out-dir PATH` | | Explicit output directory. Overrides the auto-derived `site_outputs/<domain>/` path. Used internally by the batch driver — you typically don't need this for one-off runs. |

**Examples**

```
python3 site_pipeline.py https://www.psiquantum.com
python3 site_pipeline.py https://www.psiquantum.com -c 3 --max-pages 80
python3 site_pipeline.py https://www.psiquantum.com --skip-crawl
python3 site_pipeline.py https://www.psiquantum.com -s
python3 site_pipeline.py https://www.psiquantum.com -i
```

**Output:** `site_outputs/<domain>/`
Each fresh crawl wipes that run's `crawl_output/` first — no stale files.

---

## Skip-flag semantics (`-s` and `-i`)

The cleaning scripts (`clean_actors.py`, `clean_interactions.py`) are cheap, fast, and deterministic. The LLM extraction scripts (`feed_*.py`, `interactions_*.py`) are slow and can be interrupted. The skip flags reflect that:

- **`-s` / `--skip-actors`** — skip the actor *LLM*, re-run `clean_actors` using whatever raw results are on disk, then continue normally with the interactions LLM. Useful after Ctrl+C-ing the actor extraction mid-way: the incremental save means `1_actor_results.json` already exists with partial data, and `-s` lets you continue from there.
- **`-i` / `--skip-interactions`** — skip everything through the interactions LLM. Only `clean_interactions` + helix + network actually re-run. Useful after Ctrl+C-ing the interactions step. (No reason to re-clean actors here — `2_actor_nodes.json` must already be on disk, since interactions can't have produced raw results without it.) Implies `-s`.

The implication chain:

```
-i / --skip-interactions
    → implies -s / --skip-actors
        → implies --skip-crawl   (site_pipeline only)
```

Each flag is validated up front: if you ask to skip something but a required file isn't on disk, the script exits immediately with a message pointing at the missing file.

---

## Site Pipeline Batch (many companies)

```
python3 site_pipeline_batch.py <config.json>
```

Reads a JSON file shaped like:

```json
{
  "Psiquantum": {
    "website_link": "https://www.psiquantum.com/",
    "linkedin_link": "https://www.linkedin.com/company/psiquantum/"
  },
  "D-Wave Quantum": { ... }
}
```

**Input:** drop the config at `site_input/companies.json` (mirroring `pdf_input/`).

| Flag | Shortcut | Description |
|---|---|---|
| `--crawl N` | `-c N` | Crawl depth per company (default `2`). |
| `--max-pages N` | | Max pages per company (default `10`). |
| `--only NAME ...` | | Restrict to specific company names. Case- and punctuation-insensitive. |
| `--resume` | | Skip any company whose `website/network.html` already exists. |

**Examples**

```
python3 site_pipeline_batch.py companies.json
python3 site_pipeline_batch.py companies.json --crawl 3 --max-pages 30
python3 site_pipeline_batch.py companies.json --only Psiquantum Quandela
python3 site_pipeline_batch.py companies.json --only "D-Wave Quantum"
python3 site_pipeline_batch.py companies.json --resume
```

Bare filenames are looked up in `site_input/` automatically. You can still pass an explicit path (`site_input/companies.json` or an absolute path) if you prefer.

**Output:** `site_outputs/<company_slug>/website/`

LinkedIn is intentionally not crawled — corporate LinkedIn pages serve an auth wall to anonymous visitors, so a depth-0 fetch returns a login page rather than posts. The `linkedin/` slot is reserved for when this is wired up with proper authentication or an API.

**Slug derivation from JSON keys:**
- `Amazon Braket (Amazon)` → `amazon_braket` (parentheticals stripped)
- `D-Wave Quantum` → `d_wave_quantum`
- `Quantum Computing Inc.` → `quantum_computing_inc`
- `1Qbit` → `1qbit`

**Behavior on failure:** one company crashing does not stop the batch. Failures are appended to `site_outputs/batch_failures.log` and a summary prints at the end. Resume with `--resume` to skip already-completed companies.

**Edge cases automatically skipped, with a summary at the end:**
- Empty / missing `website_link`
- `website_link` that is actually a LinkedIn URL (data-entry bug)
- Already-completed companies (when `--resume` is set)

---

## What each step writes

| File | Step | LLM call? |
|---|---|---|
| `1_actor_results.json` | Raw actor extraction | yes |
| `2_actor_nodes.json` | Cleaned + deduped actors | no |
| `3_interaction_results.json` | Raw interaction extraction | yes |
| `4_edges.json` | Cleaned + deduped edges | no |
| `5_nodes.json` | Actors + triple-helix classification | no |
| `5_edges.json` | Edges + functional-space classification | no |
| `network.html` | Interactive pyvis visualisation | — |

The two LLM steps (1 and 3) are the slow ones. Both save incrementally after each page (PDFs) or each URL (sites), so Ctrl+C or a mid-run crash keeps prior work on disk — exactly what `-s` and `-i` exist to recover from.

---

## Recovery patterns

| Situation | Command |
|---|---|
| Ctrl+C'd the actor LLM partway, want to keep what was saved | `python3 pdf_pipeline.py same.pdf -s` (or `site_pipeline.py same_url -s`) |
| Ctrl+C'd the interactions LLM partway | `python3 pdf_pipeline.py same.pdf -i` (or `site_pipeline.py same_url -i`) |
| Batch crashed partway through, want to keep going | `python3 site_pipeline_batch.py config.json --resume` |
| Want to tweak `clean_actors.py` and re-run from cleaning onwards | `python3 pdf_pipeline.py same.pdf -s` (or site equivalent) |
| Want to tweak only `clean_interactions.py` and re-run from there | `python3 pdf_pipeline.py same.pdf -i` (or site equivalent) |
| Want to re-crawl with different depth | rerun without `--skip-crawl` (wipes `crawl_output/`) |
| Want to re-run only the downstream stuff (helix + viz) | run `helix.py` and `network.py` directly in the output folder |

---

## Notes on chunk skipping in interactions

The interactions LLM only runs on chunks containing **at least 2 known actors**. Chunks with 0 or 1 known actor are silently skipped, which is why log lines may jump (e.g. "chunk 3/20" without seeing chunks 1–2). This is intentional — pairwise interactions need at least two actors in scope.
