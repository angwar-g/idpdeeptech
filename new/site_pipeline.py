#!/usr/bin/env python3
"""End-to-end pipeline for a website-based ecosystem extraction.

Mirrors pdf_pipeline.py:
    crawl -> feed -> clean actors -> interactions -> clean interactions
          -> helix enrichment -> network visualisation

Usage:
    python3 site_pipeline.py https://www.psiquantum.com --crawl 2
    python3 site_pipeline.py https://example.com -c 3 --max-pages 80
    python3 site_pipeline.py https://example.com --skip-crawl   # reuse crawl_output/
    python3 site_pipeline.py https://example.com --skip-actors  # reuse cleaned actors,
                                                                # rerun interactions onwards
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
    parser = argparse.ArgumentParser()
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
        help="Skip feed_site and clean_actors (implies --skip-crawl). "
             "Requires 2_actor_nodes_pdf.json and crawl_output/ in output dir.",
    )
    args = parser.parse_args()

    root = Path(__file__).parent.resolve()
    stem = site_stem(args.url)
    out_dir = root / "site_outputs" / stem
    out_dir.mkdir(parents=True, exist_ok=True)

    # --skip-actors implies --skip-crawl (you can't have cleaned actors without a crawl)
    skip_crawl = args.skip_crawl or args.skip_actors

    # 0. Crawl
    if skip_crawl:
        crawl_dir = out_dir / "crawl_output"
        if not crawl_dir.exists():
            sys.exit(
                f"Error: {crawl_dir} does not exist.\n"
                "Run without --skip-crawl/--skip-actors first to crawl the site."
            )
        reason = "--skip-actors" if args.skip_actors else "--skip-crawl"
        print(f"\n=== 0 crawl site (skipped via {reason}, reusing {crawl_dir}) ===")
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

    # 1, 2. Actor extraction + cleaning
    if args.skip_actors:
        nodes_file = out_dir / "2_actor_nodes_pdf.json"
        if not nodes_file.exists():
            sys.exit(
                f"Error: --skip-actors requires {nodes_file} to exist.\n"
                "Run the pipeline without --skip-actors first to generate actor nodes\n"
                "(or with only --skip-crawl if you already have a crawl)."
            )
        print("\n=== 1 actor extraction (skipped via --skip-actors) ===")
        print("\n=== 2 clean actors (skipped via --skip-actors) ===")
    else:
        run(
            "1 actor extraction",
            [sys.executable, str(root / "feed_site.py")],
            out_dir,
        )
        run(
            "2 clean actors",
            [sys.executable, str(root / "clean_actors.py")],
            out_dir,
        )

    # 3, 4. Interactions + cleaning
    run(
        "3 interaction extraction",
        [sys.executable, str(root / "interactions_site.py")],
        out_dir,
    )
    run(
        "4 clean interactions",
        [sys.executable, str(root / "clean_interactions.py")],
        out_dir,
    )

    # 5. Helix enrichment
    run(
        "5 helix enrichment",
        [
            sys.executable, str(root / "helix.py"),
            "--actors", "2_actor_nodes_pdf.json",
            "--interactions", "4_interaction_edges_pdf.json",
            "--out-actors", "5_nodes.json",
            "--out-interactions", "5_edges.json",
        ],
        out_dir,
    )

    # 6. Network visualisation
    run(
        "6 network visualisation",
        [sys.executable, str(root / "network.py")],
        out_dir,
    )

    html = out_dir / "quantum_network.html"
    if html.exists():
        html.rename(out_dir / "network.html")

    print(f"\nDone. Outputs written to: {out_dir}")


if __name__ == "__main__":
    main()
