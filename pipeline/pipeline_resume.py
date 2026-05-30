"""Resume-from-crash helper for the extraction scripts.

Each extraction script (feed_pdf, feed_site, interactions_pdf, interactions_site)
writes a progress sidecar listing (source, page) pairs that have been fully
processed. On startup the script consults the sidecar to skip already-done
work, OR an explicit --start-page N override forces a restart from page N.

Progress files live next to their data files:
    1_actor_results.json        -> 1_actor_results.progress.json
    3_interaction_results.json  -> 3_interaction_results.progress.json
"""

from __future__ import annotations

import json
from pathlib import Path


def progress_path_for(data_path: Path) -> Path:
    """E.g. Path('1_actor_results.json') -> Path('1_actor_results.progress.json')."""
    return data_path.with_suffix(".progress.json")


def load_progress(data_path: Path) -> set[tuple[str, int]]:
    """Read the (source, page) set already processed.

    If the sidecar is missing but the data file exists (e.g. from a run made
    before sidecars were introduced, or a manually-deleted sidecar), backfill
    the sidecar from the data file's (source_document, page) pairs and treat
    those as 'done'. This avoids accidentally re-doing hours of LLM work just
    because the sidecar file isn't there.

    Returns empty set if neither file exists.
    """
    sidecar = progress_path_for(data_path)

    if not sidecar.exists() and data_path.exists():
        # Backfill from the data file's records.
        try:
            records = json.loads(data_path.read_text(encoding="utf-8"))
        except Exception:
            return set()
        if not isinstance(records, list):
            return set()
        done = set()
        for rec in records:
            if not isinstance(rec, dict):
                continue
            src = rec.get("source_document")
            page = rec.get("page")
            if src is None or page is None:
                continue
            try:
                done.add((str(src), int(page)))
            except (TypeError, ValueError):
                continue
        if done:
            save_progress(data_path, done)
            print(
                f"[pipeline_resume] Backfilled {sidecar.name} from existing "
                f"{data_path.name} ({len(done)} (source, page) pairs). "
                "Run will resume from where the old data left off."
            )
        return done

    if not sidecar.exists():
        return set()

    try:
        raw = json.loads(sidecar.read_text(encoding="utf-8"))
    except Exception:
        return set()
    done = set()
    for entry in raw.get("done", []):
        try:
            done.add((str(entry["source"]), int(entry["page"])))
        except (KeyError, TypeError, ValueError):
            continue
    return done


def save_progress(data_path: Path, done: set[tuple[str, int]]) -> None:
    """Atomic write of the progress sidecar."""
    sidecar = progress_path_for(data_path)
    payload = {
        "done": sorted(
            [{"source": s, "page": p} for s, p in done],
            key=lambda x: (x["source"], x["page"]),
        )
    }
    tmp = sidecar.with_suffix(sidecar.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(sidecar)


def should_skip_page(
    source: str,
    page: int,
    done: set[tuple[str, int]],
    start_page: int | None,
) -> bool:
    """Skip this page if either:
      - the user passed --start-page N and this page is below N
      - auto-resume sees this (source, page) already in the progress sidecar
        AND no --start-page override is active

    --start-page wins: when set, we ignore the sidecar entirely so the user
    can force a re-run from page N onwards.
    """
    if start_page is not None:
        return page < start_page
    return (source, page) in done


def mark_done(
    done: set[tuple[str, int]],
    source: str,
    page: int,
    data_path: Path,
) -> None:
    """Add (source, page) to the done set and persist."""
    done.add((source, page))
    save_progress(data_path, done)


def all_complete_message(
    data_path: Path,
    done: set[tuple[str, int]],
    expected: list[tuple[str, int]],
) -> str | None:
    """Return a 'nothing to do' message if every expected (source, page) is done,
    else None.

    expected is the full list of pages the script would have processed in a
    fresh run.
    """
    if not expected:
        return None
    missing = [pair for pair in expected if pair not in done]
    if not missing:
        return (
            f"All {len(expected)} pages/URLs already processed (per "
            f"{progress_path_for(data_path).name}). Nothing to do.\n"
            f"Delete the progress sidecar to force a full re-run, or pass --start-page N."
        )
    return None
