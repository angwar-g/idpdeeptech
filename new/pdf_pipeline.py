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

    for label, script in STEPS:
        run_step(label, script, out_dir)

    print("\n=== 5 helix enrichment ===")
    subprocess.run(
        [
            sys.executable,
            str(root / "helix.py"),
            "--actors", "2_actor_nodes_pdf.json",
            "--interactions", "4_interaction_edges_pdf.json",
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

    rename_map = {
        "1_actor_results_pdf.json": f"{stem}_1_results.json",
        "2_actor_nodes_pdf.json": f"{stem}_2_actor_nodes.json",
        "3_interaction_results_pdf.json": f"{stem}_3_interaction_results.json",
        "4_interaction_edges_pdf.json": f"{stem}_4_edges.json",
        "5_nodes.json": f"{stem}_5_nodes.json",
        "5_edges.json": f"{stem}_5_edges.json",
        "quantum_network.html": f"{stem}_network.html",
    }

    for old, new in rename_map.items():
        old_path = out_dir / old
        if old_path.exists():
            shutil.copy2(old_path, out_dir / new)

    print(f"\nDone. Outputs written to: {out_dir}")

if __name__ == "__main__":
    main()