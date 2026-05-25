#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# feed - clean - inter - clean - helix - network


def run_step(label, script, cwd):
    print(f"\n=== {label} ===")
    subprocess.run(
        [sys.executable, str(Path(__file__).parent / script)],
        cwd=cwd,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run the full PDF -> actor network pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Skip flags (any of these will also skip everything before it):\n"
            "  -s  / --skip-actors        require 1_actor_results.json,        rerun clean_actors\n"
            "  -i  / --skip-interactions  require 3_interaction_results.json,  rerun clean_interactions\n"
            "\n"
            "Cleaning is cheap and always re-runs. So if you Ctrl+C the LLM mid-extraction,\n"
            "the partial raw file on disk is enough to resume with -s or -i."
        ),
    )
    parser.add_argument("pdf", help="PDF filename inside pdf_input/, e.g. china25.pdf")
    parser.add_argument(
        "--skip-actors", "-s", action="store_true",
        help="Skip feed_pdf (actor LLM). Requires 1_actor_results.json. Re-runs clean_actors.",
    )
    parser.add_argument(
        "--skip-interactions", "-i", action="store_true",
        help="Skip feed_pdf AND interactions_pdf (both LLM steps). "
             "Requires 1_actor_results.json AND 3_interaction_results.json. "
             "Re-runs both cleaning steps. Implies --skip-actors.",
    )
    args = parser.parse_args()

    # Implication: -i also implies -s.
    skip_actor_llm = args.skip_actors or args.skip_interactions
    skip_interaction_llm = args.skip_interactions

    root = Path(__file__).parent.resolve()
    pdf_path = root / "pdf_input" / args.pdf

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    stem = pdf_path.stem
    out_dir = root / "pdf_outputs" / stem
    work_pdf_dir = out_dir / "pdf_input"

    out_dir.mkdir(parents=True, exist_ok=True)
    work_pdf_dir.mkdir(exist_ok=True)

    shutil.copy2(pdf_path, work_pdf_dir / pdf_path.name)

    # Validate that required raw files exist for whatever LLM steps were skipped.
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
                "Run the pipeline without --skip-interactions first (or with only --skip-actors)\n"
                "to produce raw interaction results, then retry."
            )

    # 1. Actor extraction (LLM)
    if skip_actor_llm:
        print("\n=== 1 actor extraction (skipped, reusing 1_actor_results.json) ===")
    else:
        run_step("1 actor extraction", "feed_pdf.py", out_dir)

    # 2. Clean actors  --- ALWAYS RUNS
    run_step("2 clean actors", "clean_actors.py", out_dir)

    # 3. Interaction extraction (LLM)
    if skip_interaction_llm:
        print("\n=== 3 interaction extraction (skipped, reusing 3_interaction_results.json) ===")
    else:
        run_step("3 interaction extraction", "interactions_pdf.py", out_dir)

    # 4. Clean interactions  --- ALWAYS RUNS
    run_step("4 clean interactions", "clean_interactions.py", out_dir)

    # 5. Helix enrichment
    print("\n=== 5 helix enrichment ===")
    subprocess.run(
        [
            sys.executable,
            str(root / "helix.py"),
            "--actors", "2_actor_nodes.json",
            "--interactions", "4_edges.json",
            "--out-actors", "5_nodes.json",
            "--out-interactions", "5_edges.json",
        ],
        cwd=out_dir,
        check=True,
    )

    # 6. Network visualisation
    print("\n=== 6 network visualisation ===")
    subprocess.run(
        [sys.executable, str(root / "network.py")],
        cwd=out_dir,
        check=True,
    )

    html = out_dir / "quantum_network.html"
    if html.exists():
        html.rename(out_dir / "network.html")

    print(f"\nDone. Outputs written to: {out_dir}")


if __name__ == "__main__":
    main()
