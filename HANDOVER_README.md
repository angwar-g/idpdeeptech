# HelixNet project handover

HelixNet creates an interactive map of organizations and relationships in the
quantum ecosystem. It gathers information from PDFs, company websites, and
news articles, then displays the combined results as a network.

Public website: <https://angwar-g.github.io/idpdeeptech/>

The project has two parts:

1. **UI** — the website people use to explore the network.
2. **Pipeline** — the process that reads sources and prepares the network data.

## Project folders

```text
docs/                    The public website
docs/data/               The data currently shown on the website
pipeline/pdf_input/      PDFs that the pipeline will process
pipeline/site_input/     Lists of websites and news articles
pipeline/pdf_outputs/    Results from PDFs
pipeline/site_outputs/   Results from company websites
pipeline/news_outputs/   Results from news articles
pipeline/merged_outputs/ The final combined data and quality report
research/                Supporting research tools
deprecated/              Old files that are no longer used
```

Do not use files in `deprecated/` for normal work.

# UI section

## What the UI does

The UI is the public-facing network explorer in `docs/`.

It allows people to:

- filter the network by source, actor, and year;
- search for a particular actor;
- choose how many levels of neighbouring actors to display;
- click actors to see their classification and sources;
- click relationships to see their type, sources, and supporting evidence; and
- see how actors are distributed across the following Helix categories: Government, Industry, Academia,
  Intermediary, Civil Society, and Unknown.

The main UI files are:

```text
docs/index.html   Page content
docs/app.js       Search, filters, and network behavior
docs/style.css    Colors and visual appearance
docs/logo.png     Project logo
docs/data/        Data displayed on the website
```

## Viewing the UI locally

Open a terminal in the main project folder and run:

```bash
python -m http.server 8000
```

Then open:

<http://localhost:8000/docs/>

Do not open `index.html` by double-clicking it. The data may not load correctly
that way.

## How the filters work

- **Source document:** filters by a PDF or website.
- **Actor:** filters by an organization or other actor.
- **Year:** filters by the year associated with the source.
- **Neighbour depth:** controls how far the network expands from a selected
  actor.
- **Reset filters:** returns to the main network.

The opening view shows the largest connected part of the network. Smaller
groups and actors without relationships are hidden until they are searched for
or selected.

## Updating the public UI data

The UI reads these files:

```text
docs/data/combined_nodes.json
docs/data/combined_edges.json
```

They are created automatically when the final merge is run:

```bash
cd pipeline
python merge_all.py
```

After this command, test the UI locally before publishing it.

## UI checks before publishing

Check that:

- the page loads without an error;
- the network appears;
- source, actor, and year filters work;
- reset works;
- actor and relationship details open;
- the visible node and edge counts change when filtering; and
- the website still works on a smaller browser window.

## Common UI problems

### The page is empty or shows a loading error

Make sure:

- the local server was started from the main project folder;
- the address is `http://localhost:8000/docs/`; and
- both files exist in `docs/data/`.

### New pipeline results are not visible

Run:

```bash
cd pipeline
python merge_all.py
```

Then refresh the page. If necessary, use a hard refresh. This only updates the
version running on localhost. The public UI link will not change until the
updated files are committed and pushed. Check that everything looks correct
on localhost before pushing.

### Some actors seem to be missing

The opening view only shows the largest connected group. Search for the actor
using the Actor filter.

# Pipeline section

## What the pipeline does

The pipeline:

1. reads a PDF or website;
2. identifies actors such as companies, universities, governments, and
   research organizations;
3. identifies relationships between those actors;
4. removes duplicate or low-quality results;
5. classifies actors;
6. creates a network for checking each source; and
7. combines all completed sources into the public network.

The overall flow is:

```text
PDFs, websites, and news articles
               ↓
     Actor and relationship extraction
               ↓
       Cleaning and classification
               ↓
       Individual source networks
               ↓
          Final combined network
               ↓
            Public website
```

## First-time setup

Python 3.10 or newer is recommended.

From the main project folder:

```bash
python -m venv .venv
```

Activate the environment.

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
```

macOS/Linux:

```bash
source .venv/bin/activate
```

Install the required packages:

```bash
python -m pip install -r requirements.txt
playwright install chromium
```

The pipeline also needs access to an AI model. It supports:

- **Ollama** for a model running on the same computer; or
- **Cloudflare Workers AI** for a remote model.

The connection settings belong in a private `.env` file in the main project
folder. Do not commit this file because it may contain an API token.

Refer to `.env.example` for the required settings and use it as a template
when creating the private `.env` file.

Test the connection before processing data:

```bash
cd pipeline
python test_llm.py
```

## Processing one PDF

1. Put the PDF in `pipeline/pdf_input/`.
2. Open a terminal in `pipeline/`.
3. Run:

```bash
python pdf_pipeline.py China25.pdf
```

Replace `China25.pdf` with the real filename.

The results will be saved under:

```text
pipeline/pdf_outputs/China25/
```

## Processing several PDFs

From `pipeline/`:

```bash
python pdf_pipeline_batch.py --workers 4
```

This processes unfinished PDFs in `pdf_input/`. Completed PDFs are skipped.

If running the AI model locally with Ollama, use one worker:

```bash
python pdf_pipeline_batch.py --workers 1
```

To process only selected PDFs:

```bash
python pdf_pipeline_batch.py --only China25.pdf Japan25.pdf
```

## Processing one company website

From `pipeline/`:

```bash
python site_pipeline.py https://www.example.com/ --crawl 3 --max-pages 20
```

The results will be saved under `pipeline/site_outputs/`.

The command normally visits up to 20 pages of the website. This process is
called **crawling**: it starts at the given web address and automatically
follows links to other pages on the same website so their content can also be
collected. `--crawl 3` controls how many levels of links it follows, while
`--max-pages 20` limits the total number of pages collected.

## Processing one news article

From `pipeline/`:

```bash
python site_pipeline.py https://example.com/article \
  --news --crawl 0 --max-pages 1
```

News results are saved under `pipeline/news_outputs/`.

## Processing a list of websites

Website lists are JSON files under `pipeline/site_input/`. Their format is:

```json
{
  "IonQ": {
    "website_link": "https://ionq.com/"
  },
  "PsiQuantum": {
    "website_link": "https://www.psiquantum.com/"
  }
}
```

Process the standard company list:

```bash
cd pipeline
python site_pipeline_batch.py companies.json --workers 4
```

For a local Ollama model, use `--workers 1`.

To process selected entries only:

```bash
python site_pipeline_batch.py companies.json --only IonQ PsiQuantum
```

## Processing a list of news articles

From `pipeline/`:

```bash
python site_pipeline_batch.py news.json \
  --news --crawl 0 --max-pages 1 --workers 4
```

For news articles, `--max-pages` should be `1` because each article is treated
as a single page. If the news batch is accidentally started with a different
page limit or crawl setting, the code displays a warning and pauses briefly
before continuing, giving you time to stop it and rerun the correct command.

Use `--workers 1` for a typical local Ollama setup.

## Output from each source

Each processed source has its own output folder. The most useful files are:

```text
1_actor_results.json       Actors identified by the AI
2_actor_nodes.json         Cleaned actors
3_interaction_results.json Relationships identified by the AI
4_edges.json               Cleaned relationships
5_nodes.json               Final classified actors
5_edges.json               Final classified relationships
network.html               Network for checking this source
run.log                    Record of what happened during processing
```

There are also `.progress.json` files. These allow an interrupted run to
continue without starting over. Do not delete them unless a full restart is
intended.

## Checking a completed source

Before adding a source to the final network:

1. Open its `network.html`.
2. Check that the main actors are present.
3. Check that actor names do not contain obvious errors.
4. Check that relationships make sense.
5. Read `run.log` if the result looks incomplete.

The extraction uses AI, so results should be reviewed rather than assumed to
be correct.

## Restarting interrupted work

Usually, run the same command again. The pipeline saves progress and continues
from completed pages.

Do not use `--force` unless the existing results should be replaced.

Useful options:

```text
--skip-crawl          Reuse an existing website crawl
--skip-actors         Reuse actor extraction
--skip-interactions   Reuse actor and relationship extraction
--force               Replace the stages that are being rerun
```

Examples:

```bash
# Redo a website without downloading its pages again
python site_pipeline.py URL --force --skip-crawl

# Keep actors but redo relationships
python pdf_pipeline.py Document.pdf --force --skip-actors

# Keep AI results but redo cleaning and classification
python pdf_pipeline.py Document.pdf --force --skip-interactions
```

If unsure, first copy the source's output folder before using `--force`.

## Combining all completed sources

Preview the merge:

```bash
cd pipeline
python merge_all.py --dry-run
```

If the preview looks reasonable, run:

```bash
python merge_all.py
```

This creates:

```text
pipeline/merged_outputs/combined_nodes.json
pipeline/merged_outputs/combined_edges.json
pipeline/merged_outputs/merge_report.json
```

It also copies the combined nodes and edges into `docs/data/` for the UI.

## Correcting names and adding manual records

`pipeline/merge_rewrites.json` contains source-specific name corrections. Use
it when one source refers to the same actor in an unusual way.

For example, a country report might use “we” to mean that country. Corrections
should be limited to the affected source so unrelated actors are not combined.

`pipeline/merge_fixtures.json` contains deliberately added actors or
relationships that were not extracted from a source. Use this only when a
manual addition is intentional, and record why it was added.

After changing either file, preview and rerun the merge.

## Reviewing the final merge

After every merge, open:

```text
pipeline/merged_outputs/merge_report.json
```

Check:

- how many sources were included;
- the number of actors and relationships before and after merging;
- warnings about conflicting actor classifications;
- skipped records;
- applied name corrections; and
- manual records that were added or skipped.

Large or unexpected changes should be investigated before publishing.

## Logs and failed runs

Each source has a `run.log`.

Batch runs also create:

```text
pdf_outputs/batch_logs/
site_outputs/batch_logs/
news_outputs/batch_logs/
```

Failed batch items are listed in `batch_failures.log` under the relevant output
folder.

If a run fails:

1. Open the source's `run.log` and read the last entries.
2. Check the internet connection and AI connection.
3. Check that there is enough disk space.
4. Run `python test_llm.py`.
5. Run the same pipeline command again to continue.
6. Use `--force` only if the existing partial results should be replaced.

## Publishing a data update

After processing and checking the sources:

1. Run `python merge_all.py --dry-run`.
2. Review the preview.
3. Run `python merge_all.py`.
4. Review `merged_outputs/merge_report.json`.
5. Start the local UI and check the network.
6. Confirm the new files under `docs/data/` are included in the changes.
7. Commit and push the changes.
8. Check the public website after GitHub Pages updates.

## Routine update checklist

- [ ] Activate the Python environment.
- [ ] Run `python test_llm.py` from `pipeline/`.
- [ ] Add or update the source files/lists.
- [ ] Run the correct PDF, website, or news pipeline.
- [ ] Review failures and logs.
- [ ] Check a sample of the individual `network.html` files.
- [ ] Run `python merge_all.py --dry-run`.
- [ ] Run `python merge_all.py`.
- [ ] Review `merge_report.json`.
- [ ] Test the UI locally.
- [ ] Commit and push both merged data and `docs/data/`.
- [ ] Check the public website.

## Where to look when something needs changing

| Need | File or folder |
|---|---|
| Change the page appearance | `docs/style.css` |
| Change page text or structure | `docs/index.html` |
| Change filters or graph behavior | `docs/app.js` |
| Add PDFs | `pipeline/pdf_input/` |
| Change the company/news lists | `pipeline/site_input/` |
| Correct actor names during merging | `pipeline/merge_rewrites.json` |
| Add an intentional manual record | `pipeline/merge_fixtures.json` |
| Review a failed source | Its `run.log` |
| Review the final data quality | `pipeline/merged_outputs/merge_report.json` |

## Contact

We hope you have fun using and developing this project! If you have any
questions, feel free to contact us:

- **Name:** `Amishi Gangwar`
  - **Email:** `gangwaramishi@gmail.com`
- **Name:** `Lourenço Vieira`
  - **Email:** `lourenco.vieira.munich@gmail.com`