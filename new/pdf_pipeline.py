#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# feed - clean - inter - clean - helix - network

STEPS = [
    ("1 actor extraction", "feed_pdf.py"),
    ("2 clean actors", "clean_actors.py"),
    ("3 interaction extraction", "interactions_pdf.py"),
    ("4 clean interactions", "clean_interactions.py"),
]


def run_step(label, script, cwd):
    print(f"\n=== {label} ===")
    subprocess.run(
        [sys.executable, str(Path(__file__).parent / script)],
        cwd=cwd,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pdf", help="PDF filename inside pdf_input/, e.g. china25.pdf")
    parser.add_argument(
        "--skip-actors", "-s", action="store_true",
        help="Skip feed_pdf and clean_actors. Requires 2_actor_nodes.json in output dir.",
    )
    args = parser.parse_args()

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

    if args.skip_actors:
        nodes_file = out_dir / "2_actor_nodes.json"
        if not nodes_file.exists():
            sys.exit(
                f"Error: --skip-actors requires {nodes_file} to exist.\n"
                "Run the pipeline without --skip-actors first to generate actor nodes."
            )
        print("\n=== 1 actor extraction (skipped via --skip-actors) ===")
        print("\n=== 2 clean actors (skipped via --skip-actors) ===")
        steps_to_run = STEPS[2:]  # interactions onwards
    else:
        steps_to_run = STEPS

    for label, script in steps_to_run:
        run_step(label, script, out_dir)

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
