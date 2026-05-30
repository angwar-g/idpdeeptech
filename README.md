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
| `--start-page N` | `-p N` | Force the active LLM step to start at page N (1-indexed). Goes to actor LLM by default, or to interactions LLM when `-s` is also set. Cannot combine with `-i`. |
| `--force` | `-f` | Re-run even if `network.html` already exists. Without this, an existing run triggers an early exit to avoid accidentally redoing expensive work. |

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
| `--start-page N` | `-p N` | Force the active LLM step to start at URL ordinal N (1-indexed, in sorted crawl order). Goes to actor LLM by default, or to interactions LLM when `-s` is also set. Cannot combine with `-i`. |
| `--force` | `-f` | Re-run even if `network.html` already exists. Without this, an existing run triggers an early exit to avoid accidentally redoing the crawl + LLM work. |
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

## Choosing the LLM provider

All four extraction scripts route their LLM calls through `llm_client.py`, which switches between providers based on environment variables. The pipeline code is identical regardless of where inference runs — only the env vars change.

### Ollama (local laptop, default)

No setup beyond having Ollama running. Defaults:
```
LLM_PROVIDER=ollama          # or unset
LLM_MODEL=mistral            # or any Ollama model name
OLLAMA_BASE_URL=http://localhost:11434
```

### Cloudflare Workers AI (remote)

Set in your shell or `.env`:
```
export LLM_PROVIDER=cloudflare
export LLM_MODEL=@cf/meta/llama-3.1-8b-instruct
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...     # token with "Workers AI Read" permission
```

Same scripts, same commands. The pipeline doesn't know or care.

Why Llama 3.1 8B? Comparable model size to local Mistral 7B (so prompts behave similarly), generally better at structured JSON output, cheap per call on Workers AI. Test against your existing Mistral output on one PDF before committing — switch to a bigger model only if you see real quality regressions.

Check the live model catalog at `https://developers.cloudflare.com/workers-ai/models/`.

---

## PDF Pipeline Batch

```
python3 pdf_pipeline_batch.py
```

Runs `pdf_pipeline.py` for every `*.pdf` in `pdf_input/`. **Already-completed PDFs are skipped by default** (anything with an existing `pdf_outputs/<stem>/network.html`).

| Flag | Shortcut | Description |
|---|---|---|
| `--only NAME ...` | | Restrict to specific PDFs (filename or stem). |
| `--workers N` | `-w N` | Run N PDFs in parallel (default 1). |
| `--force` | `-f` | Redo every queued PDF from scratch, clearing raw LLM outputs and progress sidecars. Forwarded to each per-PDF pipeline. Cannot combine with `--resume`. |
| `--resume` | | (Default behavior, kept for explicitness.) Skip PDFs whose `network.html` already exists. |

**Examples**

```
python3 pdf_pipeline_batch.py                            # all not-yet-done PDFs, sequential
python3 pdf_pipeline_batch.py --workers 4                # 4 PDFs in parallel
python3 pdf_pipeline_batch.py --only china25.pdf         # one specific PDF
python3 pdf_pipeline_batch.py --workers 4 --force        # redo everything
```

---

## Parallelism (`--workers N` in both batch scripts)

Each worker runs an independent pipeline subprocess chain for one document. Workers don't share state — each writes to its own output folder, so there's no risk of race conditions on the progress sidecars or output JSONs.

**When to use `--workers > 1`:**
- Using Cloudflare or another remote LLM: yes, 4-8 workers are a free win.
- Local Ollama on a laptop with a small GPU: no, you'll just queue requests at the single Ollama backend and gain nothing. Keep `--workers 1`.
- Local Ollama on a server with a beefy GPU: yes, depending on GPU memory.

**Trade-off:** terminal output from parallel workers interleaves. Each document's clean trace is still in its own `<output_dir>/run.log` — so for clean per-document logs, read the log files after the fact rather than watching the terminal.

**Crash recovery still works.** The progress sidecar mechanism is per-document, so a crash in one worker doesn't affect the others. Re-running the bare command skips already-completed documents (default behavior).

---

## Site Pipeline Batch (many companies)

```
python3 site_pipeline_batch.py
```

Reads `site_input/companies.json` by default. The JSON is shaped like:

```json
{
  "Psiquantum": {
    "website_link": "https://www.psiquantum.com/",
    "linkedin_link": "https://www.linkedin.com/company/psiquantum/"
  },
  "D-Wave Quantum": { ... }
}
```

**Already-completed companies are skipped by default** (anything with an existing `site_outputs/<slug>/website/network.html`).

| Flag | Shortcut | Description |
|---|---|---|
| `config` (positional) | | Optional path to JSON file (default: `site_input/companies.json`). Bare filenames are looked up in `site_input/`. |
| `--crawl N` | `-c N` | Crawl depth per company (default `2`). |
| `--max-pages N` | | Max pages per company (default `10`). |
| `--only NAME ...` | | Restrict to specific JSON keys (the human-readable names). Case- and punctuation-insensitive. |
| `--workers N` | `-w N` | Run N companies in parallel (default 1). |
| `--force` | `-f` | Redo every queued company from scratch (re-crawl + full LLM). Forwarded to each per-company pipeline. Cannot combine with `--resume`. |
| `--resume` | | (Default behavior, kept for explicitness.) Skip companies whose `website/network.html` already exists. |

**Examples**

```
python3 site_pipeline_batch.py                              # all not-yet-done, sequential
python3 site_pipeline_batch.py --workers 4                  # 4 companies in parallel
python3 site_pipeline_batch.py --only Psiquantum Quandela
python3 site_pipeline_batch.py --only "D-Wave Quantum"
python3 site_pipeline_batch.py --workers 4 --force          # re-crawl + redo everything
python3 site_pipeline_batch.py myconfig.json                # use a different config in site_input/
```

`--only` matches against the **JSON keys** (the human-readable name on the left of each entry), not URLs or output folder slugs. The match is tolerant: case-insensitive, ignores spaces, dashes, and punctuation. So `Psiquantum`, `psiquantum`, and `PSI-QUANTUM` all match the JSON key `"Psiquantum"`. Multi-word names should be quoted: `--only "D-Wave Quantum"`.

**Output:** `site_outputs/<company_slug>/website/`

LinkedIn is intentionally not crawled — corporate LinkedIn pages serve an auth wall to anonymous visitors, so a depth-0 fetch returns a login page rather than posts. The `linkedin/` slot is reserved for when this is wired up with proper authentication or an API.

**Slug derivation from JSON keys** (used for output folder name only — you never type it):
- `Amazon Braket (Amazon)` → `amazon_braket` (parentheticals stripped)
- `D-Wave Quantum` → `d_wave_quantum`
- `Quantum Computing Inc.` → `quantum_computing_inc`
- `1Qbit` → `1qbit`

**Behavior on failure:** one company crashing does not stop the batch. Failures are appended to `site_outputs/batch_failures.log` and a summary prints at the end. Simply re-running the bare command picks up where it left off (default-skip).

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

The two LLM steps (1 and 3) are the slow ones. Both save incrementally after each page (PDFs) or each URL (sites), and write a progress sidecar — so a crash or Ctrl+C keeps prior work on disk, and just re-running the same command picks up where it stopped. See the Resuming after a crash section below.

---

## Recovery patterns

| Situation | Command |
|---|---|
| Laptop crashed / Ctrl+C mid-extraction | Just re-run the same command. Auto-resume picks up at the page/URL where it stopped. |
| Batch crashed partway through | `python3 pdf_pipeline_batch.py --workers 4` (or site equivalent) — default-skip handles it. |
| Want to tweak `clean_actors.py` and re-run from cleaning onwards | `python3 pdf_pipeline.py same.pdf -s` |
| Want to tweak only `clean_interactions.py` and re-run from there | `python3 pdf_pipeline.py same.pdf -i` |
| Want to redo a single completed PDF from scratch | `python3 pdf_pipeline.py same.pdf --force` |
| Want to redo every completed PDF from scratch | `python3 pdf_pipeline_batch.py --workers 4 --force` |
| Want to re-crawl a site with different depth | `python3 site_pipeline.py same_url -c 3 --force` |
| Want to re-run only the downstream stuff (helix + viz) | run `helix.py` and `network.py` directly in the output folder |

---

## Resuming after a crash

The pipelines skip already-done work at three independent levels of granularity. You don't have to manage any of this — just re-run your command and everything composes correctly. This section explains *what* gets skipped *where* so you can predict behavior.

### Level 1: batch driver skips completed documents

`pdf_pipeline_batch.py` and `site_pipeline_batch.py` check `network.html` per document before queueing. If it exists, the whole document is skipped (and not handed to a worker at all). **This is the default**; `--force` overrides it.

### Level 2: per-document pipeline guard

`pdf_pipeline.py` and `site_pipeline.py` do the same check for their own document at startup: if `network.html` exists and you didn't pass `--force` or any skip flag (`-s`/`-i`), they exit immediately. This protects the standalone single-document scripts. `--force` bypasses it.

### Level 3: per-page auto-resume inside the LLM steps

`feed_pdf.py`, `feed_site.py`, `interactions_pdf.py`, and `interactions_site.py` each write a progress sidecar next to their output:

```
1_actor_results.json            <- the actual extracted data
1_actor_results.progress.json   <- which (source, page) pairs are done
```

On startup, each script reads the sidecar and skips pages already marked done. The LLM only runs on missing pages. If every expected page is already covered, the script prints "Nothing to do" and exits.

**Where this kicks in:** if your laptop dies mid-`feed_pdf.py` on page 7 of 20, the sidecar marks pages 1-6 as done. Next run resumes at page 7. Pages 1-6 are not re-extracted.

**Backfill from old runs.** If the sidecar is missing but the raw data file exists (from runs made before sidecars existed, or after manually deleting a sidecar), the resume helper reconstructs the sidecar from the data file's `(source_document, page)` pairs on next startup. Old folders won't accidentally re-trigger hours of LLM extraction.

### How the three levels interact

A typical overnight crash scenario: you ran `pdf_pipeline_batch.py --workers 4` last night on 12 PDFs. By morning, 7 finished, 1 crashed mid-extraction, 4 weren't started. You re-run the same command:

- The 7 completed PDFs → Level 1 skips them (their `network.html` exists).
- The 1 crashed PDF → Level 1 queues it (no `network.html`). The per-PDF pipeline runs `feed_pdf.py`, which Level 3 sees the partial sidecar and picks up at the page after the crash.
- The 4 not-yet-started PDFs → Level 1 queues them, Level 3 starts at page 1 each.

No flags needed. Just re-run.

### Manual override: `-p N` / `--start-page N`

If you want to force a restart at a specific page (e.g. after tweaking a prompt), pass `-p N`. This ignores the sidecar and starts at page N regardless of what's already done. Routes to the actor LLM by default, or to the interactions LLM when `-s` is also passed.

### Force a complete redo

Easiest way: `--force` on the pipeline (single or batch). This deletes the raw LLM data and progress sidecars before starting, so Level 3 sees empty state and processes every page.

Manual alternative: `rm -rf pdf_outputs/<stem>/` then re-run. Wipes everything including the intermediate cleaned files.

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
