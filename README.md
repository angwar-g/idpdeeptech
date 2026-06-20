# Pipeline Cheat Sheet

Extracts actor/interaction graphs from PDFs and websites. Four entry points; all share the same cleaning, helix, and visualisation steps downstream.

## Setup

```bash
pip install -r requirements.txt
playwright install chromium       # browser engine used by crawl4ai
cp .env.example .env              # edit to point at the used LLM
```

`.env` keys: `LLM_PROVIDER` (`cloudflare` or `ollama`), `LLM_MODEL`, and the matching credentials. For Cloudflare set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`. For Ollama, install Ollama separately (`curl -fsSL https://ollama.com/install.sh | sh`) and pull a model (`ollama pull mistral`).

Quick check that the LLM side is wired up: `python3 test_llm.py` fires one short call and prints the round-trip time.

## Layout

```
pdf_input/*.pdf                       site_input/companies.json    site_input/news.json
       ↓                                       ↓                            ↓
pdf_pipeline(_batch).py            site_pipeline(_batch).py        site_pipeline_batch.py --news
       ↓                                       ↓                            ↓
pdf_outputs/<stem>/                    site_outputs/<slug>/         news_outputs/<slug>/
   network.html  +  5_nodes.json  +  5_edges.json  +  intermediates
```

Slugs for sites come from the URL: `https://ionq.com/` → `ionq`. Same in single and batch.
News URLs include the path: `https://thequantuminsider.com/2019/12/02/amazon-primed/` → `thequantuminsider_2019_12_02_amazon_primed`.

## Single document

```bash
python3 pdf_pipeline.py china25.pdf
python3 site_pipeline.py https://www.psiquantum.com/
```

Errors out cleanly if `network.html` already exists - pass a flag to override.

| Flag | What |
|---|---|
| `-s`, `--skip-actors` | Keep `1_actor_results.json`. Re-run interactions onwards (if missing). |
| `-i`, `--skip-interactions` | Keep both LLM outputs. Re-run only cleaning + helix + viz. |
| `--skip-crawl` *(site only)* | Keep `crawl_output/`. Re-run LLM steps (if missing) + downstream. |
| `-f`, `--force` | Wipe outputs of the steps that will run, then run them. |
| `-p N`, `--start-page N` | LLM step starts at page N (ignores sidecar). Routes to actor LLM, or to interactions when `-s` is also set. |

### `--force` × skip combinations

| Command | Wipes | Runs |
|---|---|---|
| `--force` | both LLM outputs | everything (sites: also re-crawls) |
| `--force -s` | interactions only | interactions + cleaning + helix + viz |
| `--force -i` | nothing | cleaning + helix + viz only |
| `--force --skip-crawl` *(site)* | both LLM outputs | actors + interactions + downstream |

Mental model: skip flags say "trust this step's data." `--force` says "redo whatever isn't being trusted."

## Batch

```bash
python3 pdf_pipeline_batch.py --workers 4
python3 site_pipeline_batch.py --workers 4
```

Skips docs with existing `network.html` by default. Walks `pdf_input/X.pdf` or reads `site_input/companies.json`.

| Flag | What |
|---|---|
| `-w N`, `--workers N` | Process N docs in parallel. Use `1` for local Ollama (single GPU). |
| `--only NAMES ...` | Restrict to specific PDFs (filename) or companies (JSON's key). |
| `-f`, `--force` | Queue and redo every doc, including completed ones. Forwards `--force` to each. |
| `-c N`, `--crawl N` *(site)* | Crawl depth per company (default 3). |
| `--max-pages N` *(site)* | Max pages crawled per company (default 20). |
| `--news` / `--no-news` *(site)* | Force news mode on/off. Auto-detected (see below). |

Skip flags (`-s`, `-i`, `--skip-crawl`) are **single-pipeline only** - not on the batch.

## News batches

A "news batch" is a config of article URLs (e.g. quantum-news articles) rather than company homepages. They get different default behaviour because each URL is a single self-contained page, not the entry point of a site to crawl.

**Auto-detection.** The site batch checks the config and switches into news mode automatically if either:

- the filename stem contains `news`, `article`, or `post` (`news.json`, `q4_articles.json`, `blog_posts.json`, etc), OR
- at least 50% of URLs in the config have paths with 3+ segments (typical of articles like `/YYYY/MM/DD/title`)

You can force the mode explicitly with `--news` (on) or `--no-news` (off).

**What news mode does.** Outputs go to `news_outputs/<slug>/` instead of `site_outputs/<slug>/`. Same layout inside — `network.html`, `5_nodes.json`, `5_edges.json`, etc. The single pipeline also accepts `--news` directly:

```bash
python3 site_pipeline.py URL --news
```

**Recommended flags for news.** Articles are single pages, so `--crawl 0 --max-pages 1`. The batch will warn (with a 5-second pause) if you run a detected news batch with company-style crawl flags.

```bash
python3 site_pipeline_batch.py news.json --crawl 0 --max-pages 1
```

`merge_all.py` walks `news_outputs/` too, so news graphs combine with PDFs and sites at merge time.

## Auto-resume

Four levels, all automatic - no flag needed:

1. Batch skips docs with `network.html`.
2. Single pipeline errors out if `network.html` exists (unless `--force` or skip flag).
3. LLM scripts read `*.progress.json` sidecars and skip pages already done, unless when --force.
4. *(Sites)* Crawl is skipped automatically if actor data (`1_actor_results.json` or its sidecar) exists - proof the crawl finished. `--force` overrides this and re-crawls.

So a crash + bare re-run picks up cleanly. The only time you need flags is when you want to *redo* something (force) or *avoid redoing* something (skip).

## Common recipes

```bash
# Overnight batch run
python3 pdf_pipeline_batch.py --workers 4

# Tweaked an interactions prompt, redo just that step for one doc
python3 pdf_pipeline.py singapore25.pdf --force -s

# Tweaked only cleaning rules, redo cleaning + viz only
python3 pdf_pipeline.py singapore25.pdf --force -i

# Redo a site without re-crawling
python3 site_pipeline.py https://psiquantum.com/ --force --skip-crawl

# Process a batch of news articles (single page each, no link-following)
python3 site_pipeline_batch.py news.json --crawl 0 --max-pages 1

# Redo everything in the batch
python3 pdf_pipeline_batch.py --workers 4 --force
```

## Combining all outputs

```bash
python3 merge_all.py
```

Walks `pdf_outputs/` and `site_outputs/`, dedupes actors cross-document, applies per-source rewrites from `merge_rewrites.json` (e.g. `"We"` → `"Japan"` in `japan25.pdf`), writes `merged_outputs/{combined_nodes.json, combined_edges.json, network.html, merge_report.json}`. `--dry-run` to preview.

## Logs

Each run writes `<output_dir>/run.log` with timestamped output. Batch failures appended to `pdf_outputs/batch_failures.log` or `site_outputs/batch_failures.log`.

## File outputs per doc

```
1_actor_results.json       # raw actor LLM output
2_actor_nodes.json         # cleaned, deduplicated actors
3_interaction_results.json # raw interaction LLM output
4_edges.json               # cleaned interactions
5_nodes.json               # actors enriched with helix classification
5_edges.json               # edges enriched with relation labels
network.html               # interactive pyvis visualisation
run.log                    # timestamped run log
*.progress.json            # per-page sidecars for auto-resume
```
