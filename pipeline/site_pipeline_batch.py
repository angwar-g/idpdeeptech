#!/usr/bin/env python3
"""Batch driver: run site_pipeline.py for every company in a JSON config.

Reads a JSON file shaped like:
    {
      "Psiquantum": {
        "website_link": "https://www.psiquantum.com/",
        "linkedin_link": "https://www.linkedin.com/company/psiquantum/"
      },
      ...
    }

For each company, runs the website pipeline into site_outputs/<slug>/website/.
LinkedIn is intentionally not processed yet (planned: site_outputs/<slug>/linkedin/).

Usage:
    python3 site_pipeline_batch.py                                 # uses site_input/companies.json
    python3 site_pipeline_batch.py myconfig.json                   # looks in site_input/
    python3 site_pipeline_batch.py site_input/myconfig.json        # explicit path also works
    python3 site_pipeline_batch.py /abs/path/to/myconfig.json      # absolute path also works
    python3 site_pipeline_batch.py --crawl 3 --max-pages 30
    python3 site_pipeline_batch.py --only Psiquantum Quandela
    python3 site_pipeline_batch.py --resume --workers 4
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
from site_pipeline import site_stem


def is_linkedin_url(url: str) -> bool:
    return "linkedin.com" in url.lower()


def normalize_for_match(text: str) -> str:
    """Lowercase + strip non-alnum, for tolerant --only matching."""
    return re.sub(r"[^a-z0-9]+", "", text.lower())


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
        "--crawl", "-c", type=int, default=2,
        help="Crawl depth passed to each site_pipeline run (default 2).",
    )
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="Max pages per company (default 10).",
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

    failures_log = root / "site_outputs" / "batch_failures.log"
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
        out_dir = root / "site_outputs" / slug

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
        })

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