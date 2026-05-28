"""Shared logging helpers for pdf_pipeline.py and site_pipeline.py.

The pipelines run several subprocesses (feed_*, clean_*, interactions_*, helix,
network). Each can be long-running and produces its own stdout. We want:
  1. Live progress in the terminal (nothing buffered for 30 minutes).
  2. The same output mirrored into a log file alongside the run's other outputs.
  3. The pipeline's own status prints to also land in the log.
  4. A run survives WSL disconnect / SSH drops: the log lives on disk, not in
     the controlling terminal's scrollback.

Usage from the pipeline:

    from pipeline_logging import open_run_log, close_run_log, log_print, run_subprocess_logged

    log = open_run_log(out_dir / "run.log", header="PDF pipeline: china25.pdf")
    try:
        log_print("=== 1 actor extraction ===", log)
        run_subprocess_logged([sys.executable, "feed_pdf.py"], cwd=out_dir, log=log)
        close_run_log(log, status="OK")
    except BaseException:
        close_run_log(log, status="FAILED")
        raise
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO


def open_run_log(path: Path, header: str = "") -> TextIO:
    """Open a log file in append mode and stamp a header for this run.

    Returns the open file handle. Pair with close_run_log() (preferred) or
    just call .close() on the handle if you don't want a finish footer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    log = path.open("a", encoding="utf-8", buffering=1)  # line-buffered

    start_monotonic = time.monotonic()
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    log.write("\n")
    log.write("=" * 72 + "\n")
    log.write(f"RUN STARTED  {timestamp}\n")
    if header:
        log.write(f"{header}\n")
    log.write("=" * 72 + "\n")
    log.flush()

    # Stash on the handle so close_run_log can compute elapsed without a
    # second argument. Attribute access on file objects is fine.
    log._run_start_monotonic = start_monotonic  # type: ignore[attr-defined]
    return log


def close_run_log(log: TextIO, status: str = "OK") -> None:
    """Write a finish footer (timestamp + elapsed) to terminal and log, then close.

    status: short string shown in the footer ("OK", "FAILED", "INTERRUPTED", ...).
    """
    end_monotonic = time.monotonic()
    end_wall = time.strftime("%Y-%m-%d %H:%M:%S")

    start_monotonic = getattr(log, "_run_start_monotonic", None)
    elapsed_str = ""
    if start_monotonic is not None:
        elapsed_str = _format_elapsed(end_monotonic - start_monotonic)

    footer_lines = [
        "",
        "=" * 72,
        f"RUN FINISHED {end_wall}  ({status})",
    ]
    if elapsed_str:
        footer_lines.append(f"Elapsed:     {elapsed_str}")
    footer_lines.append("=" * 72)

    for line in footer_lines:
        print(line)
        log.write(line + "\n")
    log.flush()
    log.close()


def _format_elapsed(seconds: float) -> str:
    """Render an elapsed-seconds value as e.g. '1h 23m 45s' or '47s'."""
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def log_print(message: str, log: TextIO) -> None:
    """Print to terminal AND log file."""
    print(message)
    log.write(message + "\n")
    log.flush()


def run_subprocess_logged(
    cmd: list[str],
    cwd: Path,
    log: TextIO,
    check: bool = True,
) -> int:
    """Run a subprocess, streaming its stdout/stderr to terminal AND log file.

    Each line is forwarded as soon as the child flushes it, so live progress
    works (no waiting for the process to finish). Combines stderr into stdout
    so ordering is preserved. Returns the subprocess return code; raises
    CalledProcessError when check=True and the code is nonzero.
    """
    # Unbuffered child stdout so tqdm-style progress also flushes promptly.
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,  # line-buffered
    )

    assert proc.stdout is not None  # for type checkers

    try:
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
    finally:
        proc.stdout.close()
        returncode = proc.wait()

    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)

    return returncode
