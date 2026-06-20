#!/usr/bin/env python3
"""Batch driver: run site_pipeline.py for every company in a JSON config.

Reads a JSON file shaped like:
    {
      "Psiquantum": {
        "website_link": "https://www.psiquantum.com/"
      },
      ...
    }

For each company, runs the website pipeline into site_outputs/<slug>/, where
<slug> is derived from the URL (see site_pipeline.site_stem). Already-completed
companies (those with an existing network.html) are skipped by default; pass
--force to redo them.

Usage:
    python3 site_pipeline_batch.py                                 # uses site_input/companies.json
    python3 site_pipeline_batch.py myconfig.json                   # looks in site_input/
    python3 site_pipeline_batch.py site_input/myconfig.json        # explicit path also works
    python3 site_pipeline_batch.py /abs/path/to/myconfig.json      # absolute path also works
    python3 site_pipeline_batch.py --crawl 3 --max-pages 30
    python3 site_pipeline_batch.py --only Psiquantum Quandela
    python3 site_pipeline_batch.py --workers 4                     # 4 in parallel
    python3 site_pipeline_batch.py --force                         # redo every company from scratch
"""
import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

# Reuse the URL-based slug helper from the single pipeline. Both entry points
# (single and batch) now produce the same folder for the same URL.
from site_pipeline import site_stem, DEFAULT_CRAWL_DEPTH, DEFAULT_MAX_PAGES


def is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in url.lower()


def normalize_for_match(text: str) -> str:
    """Lowercase + strip non-alnum, for tolerant --only matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


_NEWS_KEY_DATE_RE = re.compile(r"^\s*(\d{4}-\d{2}-\d{2})\b")


def extract_date_from_key(key: str) -> str:
    """Pull an ISO date prefix out of a news.json key.

    News keys are formatted like:
        "2018-11-09 - Princeton Researchers Discover ... [d9cb1dc0]"
    Returns "2018-11-09" or "" if no date prefix found.
    """
    m = _NEWS_KEY_DATE_RE.match(key)
    return m.group(1) if m else ""


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "config", nargs="?", default="companies.json",
        help="Path to companies JSON file (default: companies.json in site_input/).",
    )
    parser.add_argument(
        "--crawl", "-c", type=int, default=DEFAULT_CRAWL_DEPTH,
        help=f"Crawl depth passed to each site_pipeline run (default {DEFAULT_CRAWL_DEPTH}).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=DEFAULT_MAX_PAGES,
        help=f"Max pages per company (default {DEFAULT_MAX_PAGES}).",
    )
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="Restrict to these company names (case-insensitive, ignores spaces/punctuation). "
             "Example: --only Psiquantum 'D-Wave Quantum'",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="(Default behavior, kept for explicitness.) Skip companies whose "
             "website/network.html already exists.",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Redo every company from scratch, including the crawl and all LLM "
             "steps. Forwarded to each per-company pipeline run. Cannot combine "
             "with --resume.",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=1,
        help="Number of companies to process in parallel (default 1, sequential). "
             "Each worker runs an independent site_pipeline.py subprocess chain. "
             "Cloudflare Workers AI can handle ~4-8 cheaply; with local Ollama on "
             "a laptop, keep this at 1 unless you have a beefy GPU.",
    )
    parser.add_argument(
        "--news", dest="news_mode", action="store_true", default=None,
        help="Treat URLs as news articles. Routes outputs to news_outputs/ "
             "instead of site_outputs/, and forwards --news to each per-doc "
             "pipeline call. Auto-enabled if the config file looks like a news "
             "batch (filename contains news/article/post, OR most URLs have "
             "deep paths). Pass --no-news to force off.",
    )
    parser.add_argument(
        "--no-news", dest="news_mode", action="store_false",
        help="Force news mode off (overrides auto-detection).",
    )
    args = parser.parse_args()

    if args.resume and args.force:
        sys.exit("Error: --resume and --force are mutually exclusive. "
                 "--resume skips completed companies; --force redoes them.")

    root = Path(__file__).parent.resolve()
    SITE_INPUT_DIR = root / "site_input"

    # Path resolution rules:
    #   /abs/path.json            -> used as-is
    #   site_input/companies.json -> relative to script root (uses literal path given)
    #   companies.json            -> bare filename, assumed to live in site_input/
    raw = args.config
    config_path = Path(raw)
    if not config_path.is_absolute():
        if "/" in raw or "\\" in raw:
            config_path = root / config_path
        else:
            config_path = SITE_INPUT_DIR / config_path

    if not config_path.exists():
        sys.exit(
            f"Error: config file not found: {config_path}\n"
            f"Tip: place the JSON in {SITE_INPUT_DIR}/ and pass just the filename."
        )

    try:
        companies = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Error: invalid JSON in {config_path}: {e}")

    if not isinstance(companies, dict):
        sys.exit(f"Error: {config_path} must contain a JSON object keyed by company name.")

    # Detect article/news batches so we can (a) route output to news_outputs/
    # instead of site_outputs/, and (b) warn if the user is running with
    # company-style crawl settings. Two signals combined:
    #   (a) filename hint: contains "news", "article", or "post"
    #   (b) content heuristic: most URLs have deep paths (3+ segments), which
    #       is typical of article URLs (/YYYY/MM/DD/title) but not company
    #       homepages.
    # Either signal alone is enough to suspect a news batch.
    # User can override with --news (force on) or --no-news (force off).
    name_lower = config_path.stem.lower()
    looks_like_news_by_name = any(
        tok in name_lower for tok in ("news", "article", "post")
    )

    deep_path_count = 0
    sampled = 0
    for entry in companies.values():
        url = (entry or {}).get("website_link", "") if isinstance(entry, dict) else ""
        if not url:
            continue
        sampled += 1
        try:
            from urllib.parse import urlparse as _urlparse
            segments = [s for s in _urlparse(url).path.strip("/").split("/") if s]
            if len(segments) >= 3:
                deep_path_count += 1
        except Exception:
            pass
    deep_path_ratio = (deep_path_count / sampled) if sampled else 0.0
    looks_like_news_by_content = deep_path_ratio >= 0.5
    auto_detected_news = looks_like_news_by_name or looks_like_news_by_content

    # Resolve news mode: explicit CLI flag wins; otherwise auto-detect.
    if args.news_mode is None:
        is_news_batch = auto_detected_news
    else:
        is_news_batch = args.news_mode

    if auto_detected_news:
        signals = []
        if looks_like_news_by_name:
            signals.append(f"filename contains a news/article keyword ({config_path.name!r})")
        if looks_like_news_by_content:
            signals.append(
                f"{deep_path_count}/{sampled} URLs have deep paths "
                f"({deep_path_ratio:.0%}, typical of articles)"
            )
        if is_news_batch:
            # News mode is on (either auto or explicit). Warn only if the
            # user's crawl flags don't match the news-batch convention.
            intended = "--crawl 0 --max-pages 1"
            actual = f"--crawl {args.crawl} --max-pages {args.max_pages}"
            print(
                f"\nDetected news/article batch (output -> news_outputs/):\n"
                + "\n".join(f"  - {s}" for s in signals)
            )
            if actual.strip() != intended.strip():
                print(
                    "\n" + "!" * 72 + "\n"
                    f"WARNING: news batch but running with:  {actual}\n"
                    f"News batches usually want:  {intended}  (one page per article, no link-following)\n\n"
                    "Press Ctrl-C now to abort and re-run with the right flags, or wait 5 seconds to continue.\n"
                    + "!" * 72
                )
                try:
                    import time as _time
                    _time.sleep(5)
                except KeyboardInterrupt:
                    sys.exit("Aborted by user.")
        else:
            # Auto-detect said news but user passed --no-news.
            print(
                f"\nNote: this config looks like a news batch but --no-news was passed, "
                f"so output goes to site_outputs/ as usual.\n"
            )

    # Output root for this batch.
    output_root_name = "news_outputs" if is_news_batch else "site_outputs"

    # Optional filter.
    if args.only:
        wanted_keys = {normalize_for_match(name) for name in args.only}
        filtered = {k: v for k, v in companies.items() if normalize_for_match(k) in wanted_keys}
        missing = wanted_keys - {normalize_for_match(k) for k in filtered.keys()}
        if missing:
            print(f"Warning: --only requested names not found in config: {sorted(missing)}")
        companies = filtered

    if not companies:
        sys.exit("No companies to process.")

    total = len(companies)
    succeeded: list[str] = []
    skipped_existing: list[str] = []
    skipped_linkedin_in_website: list[str] = []
    skipped_no_website: list[str] = []
    failed: list[tuple[str, str]] = []  # (name, reason)

    # Mirror all subsequent stdout/stderr to a batch log so we can review the
    # full terminal output later. New file per run (timestamped) -- no
    # appending to one giant file. The per-doc logs (run.log inside each
    # <root>/<slug>/) are still written by the worker pipelines.
    batch_logs_dir = root / output_root_name / "batch_logs"
    batch_logs_dir.mkdir(parents=True, exist_ok=True)
    batch_log_path = batch_logs_dir / f"batch_{time.strftime('%Y%m%d_%H%M%S')}.log"

    class _Tee:
        def __init__(self, *streams):
            self._streams = streams
        def write(self, data):
            for s in self._streams:
                try:
                    s.write(data)
                    s.flush()
                except Exception:
                    pass
        def flush(self):
            for s in self._streams:
                try:
                    s.flush()
                except Exception:
                    pass

    _batch_log_fh = batch_log_path.open("w", encoding="utf-8", buffering=1)
    sys.stdout = _Tee(sys.__stdout__, _batch_log_fh)
    sys.stderr = _Tee(sys.__stderr__, _batch_log_fh)

    # Startup banner: print the effective runtime config so it's recorded in
    # the batch log too. Useful when reviewing the file weeks later to
    # remember what flags this run used.
    print()
    print("=" * 72)
    if is_news_batch:
        print(f"NEWS BATCH RUN  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    else:
        print(f"SITE BATCH RUN  {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 72)
    print(f"  Config file:    {config_path}")
    print(f"  Output root:    {output_root_name}/")
    print(f"  Documents:      {total}")
    print(f"  Workers:        {args.workers}")
    print(f"  Crawl depth:    {args.crawl}")
    print(f"  Max pages:      {args.max_pages}")
    print(f"  Force redo:     {args.force}")
    if args.only:
        print(f"  Filter --only:  {args.only}")
    print(f"  Batch log:      {batch_log_path}")
    print("=" * 72)
    print()

    failures_log = root / output_root_name / "batch_failures.log"
    failures_log.parent.mkdir(parents=True, exist_ok=True)
    with failures_log.open("a", encoding="utf-8") as f:
        f.write(f"\n=== Batch run started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(workers={args.workers}) ===\n")

    # Build the plan first (cheap dict/path operations), then run.
    plan: list[dict] = []
    for idx, (name, links) in enumerate(companies.items(), start=1):
        website_url = (links or {}).get("website_link", "").strip()

        if not website_url:
            print(f"[{idx}/{total}] {name}: skipping (no website_link)")
            skipped_no_website.append(name)
            continue

        if is_linkedin_url(website_url):
            print(f"[{idx}/{total}] {name}: skipping (website_link is a LinkedIn URL)")
            skipped_linkedin_in_website.append(name)
            continue

        slug = site_stem(website_url)
        out_dir = root / output_root_name / slug

        if (out_dir / "network.html").exists() and not args.force:
            print(f"[{idx}/{total}] {name}: skipping (already done; pass --force to redo)")
            skipped_existing.append(name)
            continue

        out_dir.mkdir(parents=True, exist_ok=True)
        plan.append({
            "idx": idx,
            "name": name,
            "slug": slug,
            "url": website_url,
            "out_dir": out_dir,
            "date": extract_date_from_key(name) if is_news_batch else "",
        })

    # For news batches, write a manifest mapping URL -> {date, title, slug} so
    # merge_all.py can join article metadata (especially date) onto each node
    # and edge at merge time. Date stays out of the per-doc extraction path --
    # it's metadata about the article, not about the actors/interactions
    # inside it. Manifest goes next to the per-doc folders so it lives or dies
    # with the news_outputs/ tree.
    if is_news_batch:
        manifest_path = root / output_root_name / "manifest.json"
        # Merge with any existing manifest so multiple batch runs accumulate
        # rather than clobbering each other.
        if manifest_path.exists():
            try:
                existing = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
        else:
            existing = {}
        for job in plan:
            existing[job["url"]] = {
                "date": job["date"],
                "title": job["name"],
                "slug": job["slug"],
            }
        # Also include already-completed docs that were skipped above, so the
        # manifest reflects everything the news_outputs/ tree contains, not
        # just what this run processed.
        for name, links in companies.items():
            url = (links or {}).get("website_link", "").strip()
            if not url or url in existing:
                continue
            if is_linkedin_url(url):
                continue
            existing[url] = {
                "date": extract_date_from_key(name),
                "title": name,
                "slug": site_stem(url),
            }
        manifest_path.write_text(
            json.dumps(existing, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"Wrote {manifest_path} ({len(existing)} articles)")

    def run_one(job: dict) -> tuple[str, str | None]:
        """Run site_pipeline.py for one company. Returns (name, error_or_None)."""
        cmd = [
            sys.executable,
            str(root / "site_pipeline.py"),
            job["url"],
            "--crawl", str(args.crawl),
            "--max-pages", str(args.max_pages),
            "--out-dir", str(job["out_dir"]),
        ]
        if args.force:
            cmd.append("--force")
        if is_news_batch:
            cmd.append("--news")
        try:
            subprocess.run(cmd, check=True)
            return job["name"], None
        except subprocess.CalledProcessError as e:
            return job["name"], f"site_pipeline exited with code {e.returncode}"
        except Exception as e:
            return job["name"], f"unexpected error: {type(e).__name__}: {e}"

    if not plan:
        print("\nNothing to run (all companies skipped).")
    elif args.workers <= 1:
        print(f"\nRunning {len(plan)} company pipeline(s) sequentially...\n")
        for job in plan:
            print(f"\n========== [{job['idx']}/{total}] {job['name']} "
                  f"(slug: {job['slug']}) ==========")
            try:
                name, err = run_one(job)
            except KeyboardInterrupt:
                print("\nInterrupted by user. Stopping batch.")
                failed.append((job["name"], "KeyboardInterrupt"))
                break
            if err is None:
                succeeded.append(name)
            else:
                print(f"  FAILED: {err}")
                failed.append((name, err))
                with failures_log.open("a", encoding="utf-8") as f:
                    f.write(f"{name}\t{job['slug']}\t{job['url']}\t{err}\n")
    else:
        # Threads, not processes: each task is a subprocess.run() that itself
        # forks a real OS process. Threads here just block waiting on pipes,
        # so the GIL is a non-issue.
        import concurrent.futures as cf
        print(f"\nRunning {len(plan)} company pipeline(s) with {args.workers} workers...")
        print("Note: parallel terminal output interleaves. Each company's clean "
              "trace is in its own site_outputs/<slug>/website/run.log\n")

        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            future_to_job = {pool.submit(run_one, job): job for job in plan}
            try:
                for fut in cf.as_completed(future_to_job):
                    job = future_to_job[fut]
                    name, err = fut.result()
                    tag = f"[{job['idx']}/{total}] {name}"
                    if err is None:
                        print(f"  DONE: {tag}")
                        succeeded.append(name)
                    else:
                        print(f"  FAILED: {tag} -- {err}")
                        failed.append((name, err))
                        with failures_log.open("a", encoding="utf-8") as f:
                            f.write(f"{name}\t{job['slug']}\t{job['url']}\t{err}\n")
            except KeyboardInterrupt:
                print("\nInterrupted by user. Stopping batch "
                      "(in-flight workers will finish their current company).")

    # Summary.
    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    print(f"Total in config (after --only filter): {total}")
    print(f"Succeeded:                  {len(succeeded)}")
    print(f"Skipped (already done):     {len(skipped_existing)}")
    print(f"Skipped (LinkedIn in slot): {len(skipped_linkedin_in_website)}")
    print(f"Skipped (no website_link):  {len(skipped_no_website)}")
    print(f"Failed:                     {len(failed)}")

    if failed:
        print("\nFailures:")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
        print(f"\nFull failure log: {failures_log}")

    if skipped_linkedin_in_website:
        print("\nLinkedIn-in-website-slot (likely a data-entry bug in the config):")
        for name in skipped_linkedin_in_website:
            print(f"  - {name}")


if __name__ == "__main__":
    main()