#!/usr/bin/env python3
"""Batch driver: run pdf_pipeline.py for every PDF in pdf_input/.

Mirrors site_pipeline_batch.py. Walks pdf_input/ for *.pdf files, runs
pdf_pipeline.py on each, with optional parallelism. Already-completed PDFs
(those with an existing pdf_outputs/<stem>/network.html) are skipped by
default; pass --force to redo them.

Usage:
    python3 pdf_pipeline_batch.py                       # all not-yet-done PDFs, sequential
    python3 pdf_pipeline_batch.py --workers 4           # 4 in parallel
    python3 pdf_pipeline_batch.py --only china25.pdf japan25.pdf
    python3 pdf_pipeline_batch.py --force               # redo every PDF from scratch
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

PDF_DIR = Path("pdf_input")
PDF_OUTPUTS = Path("pdf_outputs")


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument(
        "--only", nargs="+", default=None,
        help="Restrict to these filenames (matched against pdf_input/). "
             "Example: --only china25.pdf japan25.pdf",
    )
    parser.add_argument(
        "--resume", action="store_true",
        help="(Default behavior, kept for explicitness.) Skip PDFs whose "
             "pdf_outputs/<stem>/network.html already exists.",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Redo every PDF from scratch, clearing raw LLM outputs and progress "
             "sidecars. Forwarded to each per-PDF pipeline run. Cannot combine "
             "with --resume.",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=1,
        help="Number of PDFs to process in parallel (default 1, sequential). "
             "Each worker runs an independent pdf_pipeline.py subprocess chain. "
             "Cloudflare Workers AI can handle ~4-8 cheaply; with local Ollama on "
             "a laptop, keep this at 1 unless you have a beefy GPU.",
    )
    args = parser.parse_args()

    if args.resume and args.force:
        sys.exit("Error: --resume and --force are mutually exclusive. "
                 "--resume skips completed PDFs; --force redoes them.")

    root = Path(__file__).parent.resolve()
    pdf_dir = root / PDF_DIR

    if not pdf_dir.exists():
        sys.exit(f"Error: {pdf_dir} not found. Drop PDFs into pdf_input/ first.")

    all_pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not all_pdfs:
        sys.exit(f"No *.pdf files in {pdf_dir}.")

    # Optional name filter.
    if args.only:
        wanted = {name.strip() for name in args.only}
        # Allow user to pass with or without .pdf suffix.
        all_pdfs = [
            p for p in all_pdfs
            if p.name in wanted or p.stem in wanted
        ]
        if not all_pdfs:
            sys.exit(f"--only matched no PDFs. Got: {sorted(p.name for p in pdf_dir.glob('*.pdf'))}")

    total = len(all_pdfs)
    succeeded: list[str] = []
    skipped_existing: list[str] = []
    failed: list[tuple[str, str]] = []

    failures_log = root / "pdf_outputs" / "batch_failures.log"
    failures_log.parent.mkdir(parents=True, exist_ok=True)
    with failures_log.open("a", encoding="utf-8") as f:
        f.write(f"\n=== Batch run started {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"(workers={args.workers}) ===\n")

    plan: list[dict] = []
    for idx, pdf_path in enumerate(all_pdfs, start=1):
        out_dir = root / "pdf_outputs" / pdf_path.stem
        # Default behavior: skip already-completed PDFs. --force overrides.
        # --resume is now redundant (it's the default) but kept for explicitness.
        already_done = (out_dir / "network.html").exists()
        if already_done and not args.force:
            print(f"[{idx}/{total}] {pdf_path.name}: skipping (already done; pass --force to redo)")
            skipped_existing.append(pdf_path.name)
            continue
        plan.append({
            "idx": idx,
            "name": pdf_path.name,
            "stem": pdf_path.stem,
        })

    def run_one(job: dict) -> tuple[str, str | None]:
        cmd = [
            sys.executable,
            str(root / "pdf_pipeline.py"),
            job["name"],
        ]
        if args.force:
            cmd.append("--force")
        try:
            subprocess.run(cmd, check=True)
            return job["name"], None
        except subprocess.CalledProcessError as e:
            return job["name"], f"pdf_pipeline exited with code {e.returncode}"
        except Exception as e:
            return job["name"], f"unexpected error: {type(e).__name__}: {e}"

    if not plan:
        print("\nNothing to run (all PDFs skipped).")
    elif args.workers <= 1:
        print(f"\nRunning {len(plan)} PDF(s) sequentially...\n")
        for job in plan:
            print(f"\n========== [{job['idx']}/{total}] {job['name']} ==========")
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
                    f.write(f"{name}\t{job['stem']}\t{err}\n")
    else:
        import concurrent.futures as cf
        print(f"\nRunning {len(plan)} PDF(s) with {args.workers} workers...")
        print("Note: parallel terminal output interleaves. Each PDF's clean "
              "trace is in its own pdf_outputs/<stem>/run.log\n")

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
                            f.write(f"{name}\t{job['stem']}\t{err}\n")
            except KeyboardInterrupt:
                print("\nInterrupted by user. Stopping batch "
                      "(in-flight workers will finish their current PDF).")

    print("\n" + "=" * 60)
    print("BATCH SUMMARY")
    print("=" * 60)
    print(f"Total PDFs (after --only filter):  {total}")
    print(f"Succeeded:                          {len(succeeded)}")
    print(f"Skipped (already done):             {len(skipped_existing)}")
    print(f"Failed:                             {len(failed)}")

    if failed:
        print("\nFailures:")
        for name, reason in failed:
            print(f"  - {name}: {reason}")
        print(f"\nFull failure log: {failures_log}")


if __name__ == "__main__":
    main()