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

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TextIO


def open_run_log(path: Path, header: str = "", tag: str = "") -> TextIO:
    """Open a log file in append mode and stamp a header for this run.

    Returns the open file handle. Pair with close_run_log() (preferred) or
    just call .close() on the handle if you don't want a finish footer.

    `tag` is a short identifier (e.g. PDF stem or company slug) that will be
    prepended to every line written through log_print / run_subprocess_logged.
    With parallel workers in the batch driver, multiple pipelines log to the
    same terminal at once; the tag tells you which document each line belongs
    to. Pass an empty string to skip tagging (default behavior).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    log = path.open("a", encoding="utf-8", buffering=1)  # line-buffered

    start_monotonic = time.monotonic()
    start_wall = time.strftime("%Y-%m-%d %H:%M:%S")
    log.write("\n")
    log.write("=" * 72 + "\n")
    log.write(f"RUN STARTED  {start_wall}\n")
    if header:
        log.write(f"{header}\n")
    log.write("=" * 72 + "\n")
    log.flush()

    # Stash on the handle so close_run_log can compute elapsed and print the
    # start time without extra arguments. Attribute access on file objects is
    # fine.
    log._run_start_monotonic = start_monotonic  # type: ignore[attr-defined]
    log._run_start_wall = start_wall            # type: ignore[attr-defined]
    log._run_tag = tag                          # type: ignore[attr-defined]
    return log


def close_run_log(log: TextIO, status: str = "OK") -> None:
    """Write a finish footer (start + end timestamps + elapsed) and close.

    status: short string shown in the footer ("OK", "FAILED", "INTERRUPTED", ...).
    """
    end_monotonic = time.monotonic()
    end_wall = time.strftime("%Y-%m-%d %H:%M:%S")

    start_monotonic = getattr(log, "_run_start_monotonic", None)
    start_wall = getattr(log, "_run_start_wall", None)
    elapsed_str = ""
    if start_monotonic is not None:
        elapsed_str = _format_elapsed(end_monotonic - start_monotonic)

    footer_lines = [
        "",
        "=" * 72,
        f"RUN FINISHED {end_wall}  ({status})",
    ]
    if start_wall:
        footer_lines.append(f"Started:     {start_wall}")
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


def _stamp_prefix(log: TextIO | None) -> str:
    """Build a '[HH:MM:SS +Hh Mm Ss | docname] ' prefix for log lines.

    docname comes from the `tag` argument to open_run_log. Skipped if empty
    or if `log` is None. Used by both log_print and run_subprocess_logged so
    every line written through the logging layer carries the same context.
    """
    wall = time.strftime("%H:%M:%S")
    start_monotonic = getattr(log, "_run_start_monotonic", None) if log else None
    tag = getattr(log, "_run_tag", "") if log else ""

    if start_monotonic is None:
        body = wall
    else:
        elapsed = _format_elapsed(time.monotonic() - start_monotonic)
        body = f"{wall} +{elapsed}"

    if tag:
        body = f"{body} | {tag}"
    return f"[{body}] "


def _write_stamped(line: str, log: TextIO) -> None:
    """Write `line` to terminal and log file, prepending a timestamp prefix.

    Empty / whitespace-only lines are passed through unstamped so blank-line
    spacing between blocks is preserved. The line is expected to end with a
    newline (or not, both work).
    """
    bare = line.rstrip("\n")
    if not bare.strip():
        # Preserve blank-line spacing cleanly, no prefix noise.
        sys.stdout.write(line if line.endswith("\n") else line + "\n")
        log.write(line if line.endswith("\n") else line + "\n")
        sys.stdout.flush()
        log.flush()
        return

    prefix = _stamp_prefix(log)
    stamped = prefix + bare + "\n"
    sys.stdout.write(stamped)
    log.write(stamped)
    sys.stdout.flush()
    log.flush()


def log_print(message: str, log: TextIO) -> None:
    """Print to terminal AND log file with a timestamp prefix on each line."""
    # Preserve any intentional internal newlines: stamp each non-empty line.
    for line in message.split("\n"):
        _write_stamped(line + "\n", log)


def _is_noise_line(line: str, state: dict) -> bool:
    """Return True if `line` is a known-cosmetic line we want to drop.

    Categories filtered:
      - litellm bedrock/sagemaker pre-load warnings (you don't use those providers)
      - aiohttp SSL transport cleanup tracebacks from event-loop-closed teardown
        (the actual response was already received when this fires)

    `state` is a dict carried across calls so we can swallow multi-line tracebacks
    once we've decided to drop the first line of one.
    """
    # Multi-line traceback suppression: once we see "Fatal error on SSL transport",
    # drop everything until we see a line that looks like a new event (a stamped
    # log line from our parent, or an = separator, or a blank line followed by
    # something normal).
    if state.get("in_ssl_cleanup"):
        # Heuristic: cleanup tracebacks consist of indented code or stack frame
        # references. Stop suppressing once we see a non-indented, non-trace line.
        stripped = line.strip()
        if not stripped:
            return True  # eat blank lines inside the traceback
        if (stripped.startswith("File ")
                or stripped.startswith("Traceback")
                or stripped.startswith("During handling")
                or stripped.startswith("OSError:")
                or stripped.startswith("RuntimeError:")
                or stripped.startswith("self.")
                or stripped.startswith("n = ")
                or stripped.startswith("raise ")
                or stripped.startswith("protocol:")
                or stripped.startswith("transport:")
                or line.startswith("    ")  # indented = stack frame body
                or line.startswith("\t")):
            return True
        # Anything else means the traceback is over.
        state["in_ssl_cleanup"] = False
        # Fall through to evaluate this line normally.

    # Single-line filters.
    if "could not pre-load bedrock-runtime" in line:
        return True
    if "could not pre-load sagemaker-runtime" in line:
        return True

    # Start of multi-line SSL-cleanup traceback.
    if "Fatal error on SSL transport" in line:
        state["in_ssl_cleanup"] = True
        return True

    return False


def run_subprocess_logged(
    cmd: list[str],
    cwd: Path,
    log: TextIO,
    check: bool = True,
) -> int:
    """Run a subprocess, streaming stamped stdout/stderr to terminal AND log file.

    Each line gets a [HH:MM:SS +Hh Mm Ss] prefix so a crashed run still tells
    you when the last line landed and how long since the run began. Live
    progress works (no waiting for the process to finish). stderr is combined
    into stdout so ordering is preserved.

    PYTHONUNBUFFERED=1 is injected into the child environment because Python's
    default stdout is BLOCK-buffered when stdout is a pipe (not a TTY). Without
    this, child print() calls accumulate ~4KB before reaching our reader, and
    long extraction loops look frozen for minutes at a time.

    Known-cosmetic noise (litellm bedrock/sagemaker preload warnings and
    aiohttp SSL-cleanup tracebacks that fire AFTER our response was received)
    is filtered out via _is_noise_line, so logs stay readable.
    """
    child_env = os.environ.copy()
    child_env["PYTHONUNBUFFERED"] = "1"

    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=child_env,
    )

    assert proc.stdout is not None  # for type checkers

    filter_state: dict = {"in_ssl_cleanup": False}
    try:
        for line in proc.stdout:
            if _is_noise_line(line, filter_state):
                continue
            _write_stamped(line, log)
    finally:
        proc.stdout.close()
        returncode = proc.wait()

    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)

    return returncode
