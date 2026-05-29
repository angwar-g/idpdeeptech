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

---

## Run logs

Every `pdf_pipeline.py` and `site_pipeline.py` run writes a `run.log` file inside the run's output directory (`pdf_outputs/<stem>/run.log` or `site_outputs/<slug>/run.log`). It captures everything the terminal showed, including each subprocess's output, streamed live.

The log is *appended* on every run (with a timestamped header per run), so re-running with `-s` or `-i` after a Ctrl+C keeps the original history.

Useful when:
- You disconnect from WSL or SSH and lose terminal scrollback — the log is still on disk.
- You want to compare a current run against a previous one in the same directory.
- A run fails partway through and you want to share or grep the output later.

To follow a run live from another shell:

```
tail -f pdf_outputs/china25/run.log
```

---

## Combining everything: `merge_all.py`

After running individual pipelines, this script walks `pdf_outputs/*/` and `site_outputs/*/` (including the batch layout `*/website/`), dedupes actors across sources, applies document-relative rewrites, and produces one combined graph.

```
python3 merge_all.py                          # do the merge
python3 merge_all.py --dry-run                # preview rewrites + merge counts, write nothing
python3 merge_all.py --no-network             # skip the network.html step
python3 merge_all.py --rewrites custom.json   # use a different rewrite map
```

**Output:** `merged_outputs/`
- `combined_nodes.json` — one record per canonical actor across all sources. Each node lists `source_documents` and a full `mentions` array preserving every occurrence.
- `combined_edges.json` — all edges with rewrites applied and actor keys repointed to the merged canonical IDs.
- `merge_report.json` — diagnostics: which rewrites fired and how often, plus any cases where the same actor was classified to different helixes across sources.
- `network.html` — combined visualisation.

### Rewrite map (`merge_rewrites.json`)

Auto-created on first run with a few starter patterns. Each rule has a source document and a regex match → replace. Apply before merging so document-relative names like "We", "the Government", "our country" get pinned to the right entity for that source.

```json
{
  "rewrites": {
    "japan25.pdf": [
      {"match": "^we$", "replace": "Japan"},
      {"match": "^(the )?government$", "replace": "Japan Government"}
    ],
    "china25.pdf": [
      {"match": "^(the )?state council$", "replace": "China State Council"}
    ],
    "*": [
      ...patterns that apply to every source
    ]
  }
}
```

Match strings are **case-insensitive regexes**. The `"*"` source key applies to every source — use carefully. As you discover new ambiguity patterns, add them and re-run with `--dry-run` first to preview impact.

### Helix conflicts

When the same actor (e.g. "Tsinghua University") appears in two documents with different helix classifications, `merge_all.py` picks the more confident record (preferring `classification_needs_review = False`, then non-Unknown helix, then richer occurrence text) and flags the conflict in `merge_report.json` for review.
