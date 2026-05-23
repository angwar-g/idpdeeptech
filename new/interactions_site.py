"""Extract interactions between known actors from crawled website pages.

Parallel to interactions_pdf.py. Reads markdown from crawl_output/*.json files.
Uses the same LLM prompt as interactions_pdf.py.

Difference: builds ONE combined actor map across the whole site (not per-document),
so an actor mentioned on the About page can still be matched in a press release
on a different URL.

Writes to 3_interaction_results.json so the existing clean_interactions.py
picks it up without modification. Saves incrementally after each URL.
"""

import json
import re
import asyncio
import warnings
from pathlib import Path

import fitz  # noqa: F401  (kept so the env mirrors interactions_pdf.py; harmless)
from litellm import acompletion
from json_repair import repair_json

warnings.filterwarnings("ignore", message="Pydantic serializer warnings:*")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")


CRAWL_DIR = Path("crawl_output")
INPUT_JSON = Path("2_actor_nodes.json")
OUTPUT_EDGES_JSON = Path("3_interaction_results.json")

VALID_STATUSES = {"entity", "actor"}
INVALID_CATEGORIES = {"", "null", "unknown"}
SKIP_REVIEW_REASONS = {"generic_fragment"}

REQUIRED_EDGE_FIELDS = {
    "source_actor",
    "target_actor",
    "interaction_phrase",
    "occurrence_sentence",
    "source_document",
    "page",
}


def normalize_name(name: str) -> str:
    name = str(name).lower().strip()
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\b(the|a|an)\b", " ", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def clean_json(raw: str) -> str:
    raw = str(raw).strip()
    raw = raw.replace("```json", "").replace("```", "").strip()
    raw = raw.replace("“", '"').replace("”", '"')
    raw = raw.replace("‘", "'").replace("’", "'")

    start = raw.find("[")
    end = raw.rfind("]")

    if start != -1 and end != -1:
        return raw[start:end + 1]

    return raw


def parse_json_array(raw: str) -> list:
    cleaned = clean_json(raw)

    try:
        parsed = json.loads(cleaned)
    except Exception:
        parsed = json.loads(repair_json(cleaned))

    return parsed if isinstance(parsed, list) else []


def split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Cut a single oversized paragraph into pieces no larger than max_chars.

    Tries sentence boundaries first, then falls back to hard char-boundary splits.
    Used to tame web-markdown blobs (nav bars, footer link soup) that arrive as
    one giant "paragraph" with no internal blank lines.
    """
    if len(paragraph) <= max_chars:
        return [paragraph]

    sentences = re.split(r"(?<=[.!?])\s+", paragraph)

    pieces: list[str] = []
    buf = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            if buf:
                pieces.append(buf)
                buf = ""
            for i in range(0, len(sentence), max_chars):
                pieces.append(sentence[i:i + max_chars])
            continue

        if len(buf) + len(sentence) + 1 > max_chars and buf:
            pieces.append(buf)
            buf = sentence
        else:
            buf = f"{buf} {sentence}".strip() if buf else sentence

    if buf:
        pieces.append(buf)

    return pieces


def paragraph_chunks(text: str, max_chars: int = 1500, overlap_paragraphs: int = 1) -> list[str]:
    raw_paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]

    paragraphs: list[str] = []
    for p in raw_paragraphs:
        paragraphs.extend(split_oversized_paragraph(p, max_chars))

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for p in paragraphs:
        if current_len + len(p) > max_chars and current:
            chunks.append("\n\n".join(current))
            current = current[-overlap_paragraphs:] if overlap_paragraphs > 0 else []
            current_len = sum(len(x) for x in current)

        current.append(p)
        current_len += len(p)

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def actor_is_valid(actor: dict) -> bool:
    status = str(actor.get("status", "")).lower().strip()
    category = str(actor.get("category", "")).lower().strip()
    review_reason = str(actor.get("review_reason", "")).lower().strip()

    if review_reason in SKIP_REVIEW_REASONS:
        return False

    if status not in VALID_STATUSES:
        return False

    if category in INVALID_CATEGORIES:
        return False

    return True


def build_actor_maps_all(all_actors: list[dict]):
    """Build one combined actor map across the whole site, ignoring source_document.

    For a single-site crawl, all crawled URLs share one ecosystem context,
    so an actor extracted from one URL should still be matchable on another.
    """
    canonical_names: list[str] = []
    alias_to_canonical: dict[str, str] = {}

    for actor in all_actors:
        if not actor_is_valid(actor):
            continue

        entity = actor.get("entity")
        if not entity:
            continue

        canonical = entity
        canonical_key = normalize_name(canonical)

        if canonical_key:
            alias_to_canonical[canonical_key] = canonical
            canonical_names.append(canonical)

        for alias in actor.get("aliases", []):
            alias_key = normalize_name(alias)
            if alias_key:
                alias_to_canonical[alias_key] = canonical

    seen = set()
    unique_canonical_names = []

    for name in canonical_names:
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            unique_canonical_names.append(name)

    return unique_canonical_names, alias_to_canonical


def build_short_aliases(actor_nodes):
    short_aliases = set()

    for actor in actor_nodes:
        for alias in actor.get("aliases", []):
            key = normalize_name(alias)
            if 2 <= len(key) <= 6 and key.isalnum():
                short_aliases.add(key)

    return short_aliases


def get_present_actors(
    chunk: str,
    alias_to_canonical: dict[str, str],
    short_aliases: set[str],
) -> list[str]:
    chunk_norm = normalize_name(chunk)
    present = []

    for alias_key, canonical in sorted(alias_to_canonical.items(), key=lambda x: len(x[0]), reverse=True):
        if not alias_key:
            continue

        if alias_key in short_aliases:
            if re.search(rf"\b{re.escape(alias_key)}\b", chunk_norm):
                present.append(canonical)
        else:
            if alias_key in chunk_norm:
                present.append(canonical)

    seen = set()
    unique = []

    for name in present:
        key = normalize_name(name)
        if key and key not in seen:
            seen.add(key)
            unique.append(name)

    return unique


async def extract_interactions_from_chunk(
    source_url: str,
    chunk_idx: int,
    chunk: str,
    actor_names: list[str],
) -> str:
    """Same prompt as interactions_pdf.py, with source_document=URL."""
    actor_list_text = "\n".join(f"- {name}" for name in actor_names)

    prompt = f"""
    You extract explicit interactions between known actors in quantum/deep-tech ecosystem texts.

    SOURCE
    source_document: "{source_url}"
    page: {chunk_idx}

    KNOWN ACTORS
    {actor_list_text}

    TEXT
    {chunk}

    TASK
    Find explicit interactions or co-participations between the known actors in the text.

    Return only a valid JSON array. Each object must have exactly these fields:

    {{
    "source_actor": "...",
    "target_actor": "...",
    "interaction_phrase": "...",
    "occurrence_sentence": "...",
    "source_document": "{source_url}",
    "page": {chunk_idx}
    }}

    RULES
    - Use only actors from the KNOWN ACTORS list.
    - Both source_actor and target_actor must be copied exactly from KNOWN ACTORS.
    - Do not invent actors.
    - Do not classify the interaction type.
    - Extract only explicit interactions or co-participation.
    - Interaction can include collaboration, funding, founding, spin-off, donation, establishment, request, cooperation, affiliation, joint development, membership, ownership, controlling shareholder, or participation in the same named initiative.
        - For list-based collaboration or co-development, extract ALL pairwise interactions between the lead/developing actor and every listed collaborator.
        - Example: "It was developed by Actor A in collaboration with Actor B, Actor C, and Actor D" must produce:
        Actor A -> Actor B
        Actor A -> Actor C
        Actor A -> Actor D
        - If multiple actors jointly announced, jointly developed, jointly established, or jointly commercialized something, extract an edge for each explicit pair.
        - Do not create edges from headers, footers, author bylines, page numbers, acknowledgments, or biography text unless they describe a substantive innovation ecosystem relationship.    
    - Do not create an interaction just because two actors are mentioned in the same document.
    - interaction_phrase must be copied from the text.
    - occurrence_sentence must be the full sentence containing the interaction.
    - If there are no interactions, return [].
    
    - Always extract spin-off edges:
        "Actor A and Actor B both spun off from Actor C" =>
        Actor A -> Actor C
        Actor B -> Actor C

        - Always extract founding edges:
        "Person A founded Actor B" =>
        Person A -> Actor B

        - Always extract donation edges:
        "Actor A received a donation from Actor B" =>
        Actor A -> Actor B

        - Always extract establishment edges:
        "Actor A, in collaboration with Actor B and Actor C, established Actor D" =>
        Actor A -> Actor D
        Actor B -> Actor D
        Actor C -> Actor D
    
    - Return only JSON. No markdown. No explanation.
    """

    response = await acompletion(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        num_predict=1500,
    )

    return response.choices[0].message.content  # type: ignore


def has_required_schema(edge: dict) -> bool:
    return set(edge.keys()) == REQUIRED_EDGE_FIELDS


def basic_valid_edge(edge: dict, valid_actor_keys: set[str]) -> bool:
    source = normalize_name(edge.get("source_actor", ""))
    target = normalize_name(edge.get("target_actor", ""))

    if not source or not target or source == target:
        return False

    if source not in valid_actor_keys or target not in valid_actor_keys:
        return False

    if not str(edge.get("interaction_phrase", "")).strip():
        return False

    if not str(edge.get("occurrence_sentence", "")).strip():
        return False

    if any(k.endswith(".") or '"' in k for k in edge.keys()):
        return False

    return True


def dedupe_edges(edges: list[dict]) -> list[dict]:
    seen = set()
    deduped = []

    for edge in edges:
        source = normalize_name(edge.get("source_actor", ""))
        target = normalize_name(edge.get("target_actor", ""))
        pair = tuple(sorted([source, target]))

        key = (
            pair,
            normalize_name(edge.get("interaction_phrase", "")),
            normalize_name(edge.get("occurrence_sentence", "")),
            str(edge.get("source_document", "")).strip(),
            str(edge.get("page", "")).strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(edge)

    return deduped


def save_edges(edges: list[dict]) -> None:
    """Write a deduped snapshot of edges to disk."""
    OUTPUT_EDGES_JSON.write_text(
        json.dumps(dedupe_edges(edges), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def main():
    if not INPUT_JSON.exists():
        print(f"Missing {INPUT_JSON}. Run clean_actors.py first.")
        return

    if not CRAWL_DIR.exists():
        print(f"No {CRAWL_DIR}/ directory found. Run crawl_site.py first.")
        return

    actors = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    short_aliases = build_short_aliases(actors)

    actor_names, alias_to_canonical = build_actor_maps_all(actors)

    if not actor_names:
        print("No valid actors found, skipping.")
        return

    valid_actor_keys = {normalize_name(name) for name in actor_names}
    all_interactions: list[dict] = []

    crawl_files = sorted(CRAWL_DIR.glob("*.json"))
    total_files = len(crawl_files)

    if not crawl_files:
        print(f"No JSON files in {CRAWL_DIR}/. Nothing to do.")
        return

    for file_idx, crawl_file in enumerate(crawl_files, start=1):
        try:
            data = json.loads(crawl_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"Skipping malformed crawl file {crawl_file.name}: {e}")
            continue

        url = data.get("url", crawl_file.stem)
        markdown = data.get("markdown", "")

        if not markdown.strip():
            continue

        chunks = paragraph_chunks(markdown, max_chars=1500, overlap_paragraphs=1)
        total_chunks = len(chunks)

        for chunk_idx, chunk in enumerate(chunks, start=1):
            present_actors = get_present_actors(chunk, alias_to_canonical, short_aliases)
            present_actors = present_actors[:25]

            if len(present_actors) < 2:
                continue

            print(
                f"Extracting interactions: {url} "
                f"({file_idx}/{total_files}), "
                f"chunk {chunk_idx}/{total_chunks}"
            )

            try:
                raw = await extract_interactions_from_chunk(
                    source_url=url,
                    chunk_idx=chunk_idx,
                    chunk=chunk,
                    actor_names=present_actors,
                )
            except Exception as e:
                print(f"Model call failed on {url}, chunk {chunk_idx}: {e}")
                continue

            try:
                parsed = parse_json_array(raw)
            except Exception:
                print("Could not parse interaction JSON:")
                print(str(raw)[:1000])
                parsed = []

            for edge in parsed:
                if not isinstance(edge, dict):
                    continue

                if not has_required_schema(edge):
                    continue

                if not basic_valid_edge(edge, valid_actor_keys):
                    continue

                all_interactions.append(edge)

            await asyncio.sleep(1)

        # Incremental save after each URL.
        save_edges(all_interactions)

    all_interactions = dedupe_edges(all_interactions)

    OUTPUT_EDGES_JSON.write_text(
        json.dumps(all_interactions, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {OUTPUT_EDGES_JSON} with {len(all_interactions)} extracted interactions.")


if __name__ == "__main__":
    asyncio.run(main())
