#!/usr/bin/env python3
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from pipeline_logging import open_run_log, close_run_log, log_print, run_subprocess_logged

# feed - clean - inter - clean - helix - network


def run_step(label, script, cwd, log, extra_args=None):
    log_print(f"\n=== {label} ===", log)
    cmd = [sys.executable, str(Path(__file__).parent / script)]
    if extra_args:
        cmd.extend(extra_args)
    run_subprocess_logged(
        cmd,
        cwd=cwd,
        log=log,
    )


def main():
    parser = argparse.ArgumentParser(
        description="Run the full PDF -> actor network pipeline.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Skip flags:\n"
            "  -s  / --skip-actors        skip actor LLM, re-run clean_actors, continue normally.\n"
            "                             Requires 1_actor_results.json on disk.\n"
            "  -i  / --skip-interactions  skip everything through interactions LLM, re-run only\n"
            "                             clean_interactions + helix + viz.\n"
            "                             Requires 1_actor_results.json, 2_actor_nodes.json,\n"
            "                             and 3_interaction_results.json on disk.\n"
            "\n"
            "Cleaning is cheap. Both skips exist so that Ctrl+C during an LLM step doesn't\n"
            "force you to re-do hours of work — the incremental save means partial raw\n"
            "results are usable.\n"
            "\n"
            "A run.log is written in the output directory and appended to on every run."
        ),
    )
    parser.add_argument("pdf", help="PDF filename inside pdf_input/, e.g. china25.pdf")
    parser.add_argument(
        "--skip-actors", "-s", action="store_true",
        help="Skip feed_pdf (actor LLM). Requires 1_actor_results.json. Re-runs clean_actors.",
    )
    parser.add_argument(
        "--skip-interactions", "-i", action="store_true",
        help="Skip actor LLM, clean_actors, AND interactions LLM. Only re-runs "
             "clean_interactions and the downstream helix/viz steps. "
             "Requires 1_actor_results.json, 2_actor_nodes.json, and "
             "3_interaction_results.json. Implies --skip-actors.",
    )
    parser.add_argument(
        "--start-page", "-p", type=int, default=None,
        help="Force the active LLM step to start at this page number "
             "(1-indexed), ignoring auto-resume. Goes to actor LLM by default, "
             "or to interactions LLM when --skip-actors is also set. "
             "Cannot be combined with --skip-interactions (which skips both LLMs).",
    )
    args = parser.parse_args()

    # Implication: -i also implies -s.
    skip_actor_llm = args.skip_actors or args.skip_interactions
    skip_interaction_llm = args.skip_interactions

    if args.start_page is not None and skip_interaction_llm:
        sys.exit(
            "Error: --start-page is incompatible with --skip-interactions.\n"
            "When --skip-interactions is set, no LLM steps run, so there is\n"
            "nothing to start at page N."
        )

    if args.start_page is not None and args.start_page < 1:
        sys.exit("Error: --start-page must be 1 or greater.")

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

    # Validate required raw files exist for whatever LLM steps were skipped.
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

    skip_summary = (
        " --skip-interactions" if skip_interaction_llm
        else " --skip-actors" if args.skip_actors
        else ""
    )
    log = open_run_log(
        out_dir / "run.log",
        header=f"pdf_pipeline.py {args.pdf}{skip_summary}",
    )

    try:
        # Decide which LLM step gets --start-page (if set).
        # Default: actor LLM. With -s: interactions LLM (since actor LLM is skipped).
        actor_extra = []
        interaction_extra = []
        if args.start_page is not None:
            if skip_actor_llm:
                interaction_extra = ["--start-page", str(args.start_page)]
            else:
                actor_extra = ["--start-page", str(args.start_page)]

        # 1. Actor extraction (LLM)
        if skip_actor_llm:
            log_print("\n=== 1 actor extraction (skipped, reusing 1_actor_results.json) ===", log)
        else:
            run_step("1 actor extraction", "feed_pdf.py", out_dir, log, extra_args=actor_extra)

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
            run_step("2 clean actors", "clean_actors.py", out_dir, log)

        # 3. Interaction extraction (LLM)
        if skip_interaction_llm:
            log_print("\n=== 3 interaction extraction (skipped, reusing 3_interaction_results.json) ===", log)
        else:
            run_step("3 interaction extraction", "interactions_pdf.py", out_dir, log, extra_args=interaction_extra)

        # 4. Clean interactions  --- ALWAYS RUNS
        run_step("4 clean interactions", "clean_interactions.py", out_dir, log)

        # 5. Helix enrichment
        log_print("\n=== 5 helix enrichment ===", log)
        run_subprocess_logged(
            [
                sys.executable,
                str(root / "helix.py"),
                "--actors", "2_actor_nodes.json",
                "--interactions", "4_edges.json",
                "--out-actors", "5_nodes.json",
                "--out-interactions", "5_edges.json",
            ],
            cwd=out_dir,
            log=log,
        )

        # 6. Network visualisation
        log_print("\n=== 6 network visualisation ===", log)
        run_subprocess_logged(
            [sys.executable, str(root / "network.py")],
            cwd=out_dir,
            log=log,
        )

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
