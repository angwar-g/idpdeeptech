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
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def site_stem(url: str) -> str:
    """Derive an output folder name from a URL (e.g. https://www.psiquantum.com -> psiquantum_com)."""
    host = urlparse(url).netloc or url
    host = host.replace("www.", "")
    host = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")
    return host or "site"


def run(label: str, cmd: list[str], cwd: Path) -> None:
    print(f"\n=== {label} ===")
    subprocess.run(cmd, cwd=cwd, check=True)


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
             "When set, overrides the auto-derived site_outputs/<domain>/ path. "
             "Used by site_pipeline_batch.py to nest outputs under company/source folders.",
    )
    args = parser.parse_args()

    # Implication chain: -i -> -s -> --skip-crawl.
    skip_interaction_llm = args.skip_interactions
    skip_actor_llm = args.skip_actors or skip_interaction_llm
    skip_crawl = args.skip_crawl or skip_actor_llm

    root = Path(__file__).parent.resolve()

    if args.out_dir:
        out_dir = Path(args.out_dir)
        if not out_dir.is_absolute():
            out_dir = root / args.out_dir
    else:
        stem = site_stem(args.url)
        out_dir = root / "site_outputs" / stem

    out_dir.mkdir(parents=True, exist_ok=True)

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

    # 0. Crawl
    if skip_crawl:
        # Pick the most specific reason flag the user passed.
        if args.skip_interactions:
            reason = "--skip-interactions"
        elif args.skip_actors:
            reason = "--skip-actors"
        else:
            reason = "--skip-crawl"
        print(f"\n=== 0 crawl site (skipped via {reason}, reusing {out_dir / 'crawl_output'}) ===")
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
        )

    # 1. Actor extraction (LLM)
    if skip_actor_llm:
        print("\n=== 1 actor extraction (skipped, reusing 1_actor_results.json) ===")
    else:
        run("1 actor extraction", [sys.executable, str(root / "feed_site.py")], out_dir)

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
        print("\n=== 2 clean actors (skipped, reusing 2_actor_nodes.json) ===")
    else:
        run("2 clean actors", [sys.executable, str(root / "clean_actors.py")], out_dir)

    # 3. Interaction extraction (LLM)
    if skip_interaction_llm:
        print("\n=== 3 interaction extraction (skipped, reusing 3_interaction_results.json) ===")
    else:
        run("3 interaction extraction", [sys.executable, str(root / "interactions_site.py")], out_dir)

    # 4. Clean interactions  --- ALWAYS RUNS
    run("4 clean interactions", [sys.executable, str(root / "clean_interactions.py")], out_dir)

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
    )

    # 6. Network visualisation
    run("6 network visualisation", [sys.executable, str(root / "network.py")], out_dir)

    html = out_dir / "quantum_network.html"
    if html.exists():
        html.rename(out_dir / "network.html")

    print(f"\nDone. Outputs written to: {out_dir}")


if __name__ == "__main__":
    main()
