#!/usr/bin/env python3
"""End-to-end pipeline for a website-based ecosystem extraction.

Mirrors pdf_pipeline.py:
    crawl -> feed -> clean actors -> interactions -> clean interactions
          -> helix enrichment -> network visualisation

Usage:
    python3 site_pipeline.py https://www.psiquantum.com --crawl 2
    python3 site_pipeline.py https://example.com -c 3 --max-pages 80
    python3 site_pipeline.py https://example.com --skip-crawl          # reuse crawl_output/
    python3 site_pipeline.py https://example.com -s                    # reuse raw actor results,
                                                                       # rerun clean_actors then continue
    python3 site_pipeline.py https://example.com -i                    # reuse everything through
                                                                       # raw interactions, only rerun
                                                                       # clean_interactions + downstream

Skip-flag implication chain (any of these will skip everything before it):
    -i  / --skip-interactions  -> implies --skip-actors -> implies --skip-crawl
    -s  / --skip-actors        -> implies --skip-crawl

Cleaning steps are cheap. -s lets you recover from a Ctrl+C of the actor LLM;
-i lets you recover from a Ctrl+C of the interactions LLM.

A run.log is written in the output directory and appended to on every run.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

from pipeline_logging import open_run_log, close_run_log, log_print, run_subprocess_logged


def site_stem(url: str) -> str:
    """Derive an output folder name from a URL.

    For company homepages (no meaningful path), uses just the host: strips
    www. and the trailing TLD if recognised. Same as before.

      https://ionq.com/              -> 'ionq'
      https://www.psiquantum.com/    -> 'psiquantum'
      https://q-ctrl.com/            -> 'q_ctrl'
      https://aws.amazon.com/        -> 'aws_amazon'
      https://global.fujitsu/        -> 'global_fujitsu'   (no known TLD,
                                                            keep both parts)

    For URLs with a meaningful path (news articles, blog posts, deep links),
    appends a slugified version of the path so multiple articles on the same
    domain land in separate folders.

      https://thequantuminsider.com/2019/12/02/amazon-primed/
          -> 'thequantuminsider_2019_12_02_amazon_primed'

      https://www.ft.com/content/abc-123
          -> 'ft_content_abc_123'

    Both site_pipeline.py (single) and site_pipeline_batch.py (batch) use this
    function, so the same URL always maps to the same site_outputs/<slug>/
    folder regardless of entry point.
    """
    # Common TLDs we recognise. If the URL ends with one of these, strip it;
    # otherwise leave the host intact (e.g. 'global.fujitsu' keeps both parts).
    KNOWN_TLDS = {
        "com", "org", "net", "io", "ai", "co", "tech", "us", "uk", "eu",
        "de", "fr", "ca", "ch", "swiss", "es", "it", "nl", "se", "au", "jp",
        "cn", "in", "br", "mx", "ru", "kr", "sg", "tw",
    }
    parsed = urlparse(url)
    host = (parsed.netloc or url).replace("www.", "")
    parts = host.split(".")
    if len(parts) >= 2 and parts[-1].lower() in KNOWN_TLDS:
        parts = parts[:-1]
    host_slug = "_".join(parts)
    host_slug = re.sub(r"[^a-z0-9]+", "_", host_slug.lower()).strip("_")

    # Path slug: empty for bare homepages so existing company outputs are
    # unchanged. Non-empty paths get slugified and appended, capped to keep
    # folder names reasonable.
    path = parsed.path.strip("/")
    if path:
        path_slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
        # Cap at ~80 chars to keep paths sane on filesystems that limit them.
        if len(path_slug) > 80:
            path_slug = path_slug[:80].rstrip("_")
        slug = f"{host_slug}_{path_slug}" if host_slug else path_slug
    else:
        slug = host_slug

    return slug or "site"


def run(label: str, cmd: list[str], cwd: Path, log) -> None:
    log_print(f"\n=== {label} ===", log)
    run_subprocess_logged(cmd, cwd=cwd, log=log)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=__doc__,
    )
    parser.add_argument("url", help="Seed URL to crawl, e.g. https://www.psiquantum.com")
    parser.add_argument(
        "--crawl", "-c", type=int, default=2,
        help="Crawl depth (default 2). Higher = follows more internal links.",
    )
    parser.add_argument(
        "--max-pages", type=int, default=10,
        help="Max pages to crawl (default 10).",
    )
    parser.add_argument(
        "--skip-crawl", action="store_true",
        help="Skip the crawl step and reuse existing crawl_output/ inside the site folder.",
    )
    parser.add_argument(
        "--skip-actors", "-s", action="store_true",
        help="Skip feed_site (actor LLM). Requires 1_actor_results.json. "
             "Re-runs clean_actors. Implies --skip-crawl.",
    )
    parser.add_argument(
        "--skip-interactions", "-i", action="store_true",
        help="Skip actor LLM, clean_actors, AND interactions LLM. Only re-runs "
             "clean_interactions and the downstream helix/viz steps. "
             "Requires 1_actor_results.json, 2_actor_nodes.json, and "
             "3_interaction_results.json. Implies --skip-actors (and --skip-crawl).",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="Explicit output directory (relative to script root, or absolute). "
             "When set, overrides the auto-derived site_outputs/<slug>/ path. "
             "Used by site_pipeline_batch.py to nest outputs under per-company folders.",
    )
    parser.add_argument(
        "--start-page", "-p", type=int, default=None,
        help="Force the active LLM step to start at this URL ordinal "
             "(1-indexed, in sorted crawl order), ignoring auto-resume. "
             "Goes to actor LLM by default, or to interactions LLM when "
             "--skip-actors is also set. Cannot be combined with "
             "--skip-interactions (which skips both LLMs).",
    )
    parser.add_argument(
        "--force", "-f", action="store_true",
        help="Re-run even if site_outputs/<domain>/network.html already exists. "
             "Without this flag, an existing network.html triggers an early exit "
             "to avoid accidentally redoing expensive work (and re-crawling).",
    )
    args = parser.parse_args()

    # Implication chain: -i -> -s -> --skip-crawl.
    skip_interaction_llm = args.skip_interactions
    skip_actor_llm = args.skip_actors or skip_interaction_llm
    # skip_crawl resolved below, after out_dir is known.

    if args.start_page is not None and skip_interaction_llm:
        sys.exit(
            "Error: --start-page is incompatible with --skip-interactions.\n"
            "When --skip-interactions is set, no LLM steps run, so there is\n"
            "nothing to start at page N."
        )

    if args.start_page is not None and args.start_page < 1:
        sys.exit("Error: --start-page must be 1 or greater.")

    root = Path(__file__).parent.resolve()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = root / args.out_dir
    else:
        out_dir = root / "site_outputs" / site_stem(args.url)

    out_dir.mkdir(parents=True, exist_ok=True)

    # If the actor LLM has produced any output, the crawl must have finished
    # cleanly (feed_site.py reads from crawl_output and would have errored
    # otherwise). So an existing 1_actor_results.json -- even just a sidecar
    # without the full data file -- is proof of a completed crawl. Skip the
    # crawl in that case to avoid wiping a successful crawl on re-run.
    # --force overrides this: it always re-crawls when the crawl step runs.
    crawl_already_done = (
        (out_dir / "1_actor_results.json").exists()
        or (out_dir / "1_actor_results.progress.json").exists()
    )
    skip_crawl = (
        args.skip_crawl
        or skip_actor_llm
        or (crawl_already_done and not args.force)
    )

    # Guard against accidentally redoing a completed run.
    # Skip flags (-s, -i) are explicit re-run intentions, so they bypass this.
    network_html = out_dir / "network.html"
    if (network_html.exists()
            and not args.force
            and not args.skip_crawl
            and not args.skip_actors
            and not args.skip_interactions):
        sys.exit(
            f"\n{network_html} already exists.\n"
            "This site appears to be fully processed. To avoid accidentally "
            "redoing expensive work (including a fresh crawl), the pipeline "
            "is exiting.\n\n"
            "Pass --force / -f to re-run from scratch (deletes crawl, raw LLM "
            "results, and progress sidecars), or --skip-crawl / --skip-actors / "
            "--skip-interactions to re-run only parts. --force can be combined "
            "with skip flags: --force --skip-actors re-does only the interactions "
            "step, --force --skip-interactions re-does only cleaning/helix/viz.\n"
        )

    # If --force was requested, clear raw outputs for whichever steps are about
    # to RUN (not the ones being skipped). This means:
    #   --force                    wipes crawl_output, both LLM outputs.
    #   --force --skip-crawl       wipes both LLM outputs, keeps crawl.
    #   --force --skip-actors      wipes only interactions output, keeps crawl
    #                              and actor data.
    #   --force --skip-interactions wipes nothing LLM-related (both steps are
    #                               skipped), but the downstream cleaning/helix/
    #                               viz still re-runs as normal.
    # NOTE: We do NOT delete crawl_output ourselves — crawl_site.py wipes it
    # when it runs, so as long as the crawl step isn't being skipped, --force
    # naturally triggers a re-crawl.
    if args.force:
        to_clear: list[Path] = []
        if not skip_actor_llm:
            to_clear += [
                out_dir / "1_actor_results.json",
                out_dir / "1_actor_results.progress.json",
            ]
        if not skip_interaction_llm:
            to_clear += [
                out_dir / "3_interaction_results.json",
                out_dir / "3_interaction_results.progress.json",
            ]
        removed = [f.name for f in to_clear if f.exists()]
        for f in to_clear:
            f.unlink(missing_ok=True)
        if removed:
            print(f"--force: cleared {', '.join(removed)}")
        elif not (skip_actor_llm or skip_interaction_llm):
            print("--force: no raw LLM files to clear (already absent)")

    # Validate required artifacts exist for whatever was skipped.
    if skip_crawl:
        crawl_dir = out_dir / "crawl_output"
        if not crawl_dir.exists():
            sys.exit(
                f"Error: {crawl_dir} does not exist.\n"
                "Run without --skip-crawl/--skip-actors/--skip-interactions first to crawl the site."
            )

    if skip_actor_llm:
        raw_actors = out_dir / "1_actor_results.json"
        if not raw_actors.exists():
            sys.exit(
                f"Error: --skip-actors/--skip-interactions requires {raw_actors} to exist.\n"
                "Run the pipeline without these flags first to produce raw actor results,\n"
                "or let it run far enough that the incremental save writes the file."
            )

    if skip_interaction_llm:
        raw_interactions = out_dir / "3_interaction_results.json"
        if not raw_interactions.exists():
            sys.exit(
                f"Error: --skip-interactions requires {raw_interactions} to exist.\n"
                "Run with only --skip-actors first to produce raw interactions, then retry."
            )

    skip_summary = (
        " --skip-interactions" if skip_interaction_llm
        else " --skip-actors" if args.skip_actors
        else " --skip-crawl" if args.skip_crawl
        else ""
    )
    log = open_run_log(
        out_dir / "run.log",
        header=f"site_pipeline.py {args.url}{skip_summary}",
        tag=out_dir.name,
    )

    try:
        # 0. Crawl
        if skip_crawl:
            if args.skip_interactions:
                reason = "--skip-interactions"
            elif args.skip_actors:
                reason = "--skip-actors"
            elif args.skip_crawl:
                reason = "--skip-crawl"
            else:
                reason = "auto (actor data already exists; pass --force to re-crawl)"
            log_print(
                f"\n=== 0 crawl site (skipped via {reason}, reusing {out_dir / 'crawl_output'}) ===",
                log,
            )
        else:
            run(
                "0 crawl site",
                [
                    sys.executable, str(root / "crawl_site.py"),
                    args.url,
                    "--crawl", str(args.crawl),
                    "--max-pages", str(args.max_pages),
                ],
                out_dir,
                log,
            )

        # Decide which LLM step gets --start-page (if set).
        actor_cmd = [sys.executable, str(root / "feed_site.py")]
        interaction_cmd = [sys.executable, str(root / "interactions_site.py")]
        if args.start_page is not None:
            sp_args = ["--start-page", str(args.start_page)]
            if skip_actor_llm:
                interaction_cmd.extend(sp_args)
            else:
                actor_cmd.extend(sp_args)

        # 1. Actor extraction (LLM)
        if skip_actor_llm:
            log_print("\n=== 1 actor extraction (skipped, reusing 1_actor_results.json) ===", log)
        else:
            run("1 actor extraction", actor_cmd, out_dir, log)

        # 2. Clean actors. Skip when -i is set, because 2_actor_nodes.json must already
        # exist (interactions can't have produced raw results without it).
        if skip_interaction_llm:
            nodes_file = out_dir / "2_actor_nodes.json"
            if not nodes_file.exists():
                sys.exit(
                    f"Error: --skip-interactions found raw interactions but no {nodes_file.name}.\n"
                    f"Expected {nodes_file} to exist already. Re-run with --skip-actors instead\n"
                    "to regenerate the cleaned actor nodes."
                )
            log_print("\n=== 2 clean actors (skipped, reusing 2_actor_nodes.json) ===", log)
        else:
            run("2 clean actors", [sys.executable, str(root / "clean_actors.py")], out_dir, log)

        # 3. Interaction extraction (LLM)
        if skip_interaction_llm:
            log_print("\n=== 3 interaction extraction (skipped, reusing 3_interaction_results.json) ===", log)
        else:
            run("3 interaction extraction", interaction_cmd, out_dir, log)

        # 4. Clean interactions  --- ALWAYS RUNS
        run("4 clean interactions", [sys.executable, str(root / "clean_interactions.py")], out_dir, log)

        # 5. Helix enrichment
        run(
            "5 helix enrichment",
            [
                sys.executable, str(root / "helix.py"),
                "--actors", "2_actor_nodes.json",
                "--interactions", "4_edges.json",
                "--out-actors", "5_nodes.json",
                "--out-interactions", "5_edges.json",
            ],
            out_dir,
            log,
        )

        # 6. Network visualisation
        run("6 network visualisation", [sys.executable, str(root / "network.py")], out_dir, log)

        html = out_dir / "quantum_network.html"
        if html.exists():
            html.rename(out_dir / "network.html")

        log_print(f"\nDone. Outputs written to: {out_dir}", log)
        log_print(f"Log: {out_dir / 'run.log'}", log)
        close_run_log(log, status="OK")
    except KeyboardInterrupt:
        close_run_log(log, status="INTERRUPTED")
        raise
    except BaseException:
        close_run_log(log, status="FAILED")
        raise


if __name__ == "__main__":
    main()
