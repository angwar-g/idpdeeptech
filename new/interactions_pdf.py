import json
import re
import asyncio
from pathlib import Path
import warnings

import fitz  # type: ignore PyMuPDF
from litellm import acompletion
from json_repair import repair_json

warnings.filterwarnings("ignore", message="Pydantic serializer warnings:*")
warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

PDF_DIR = Path("pdf_input")
INPUT_JSON = Path("2_actor_nodes_pdf.json")
OUTPUT_EDGES_JSON = Path("3_interaction_results_pdf.json")

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


def extract_pdf_text(pdf_path: Path) -> list[dict]:
    doc = fitz.open(pdf_path)
    pages = []

    for page_num, page in enumerate(doc, start=1):  # type: ignore
        pages.append({
            "page": page_num,
            "text": page.get_text("text"),
        })

    return pages


def paragraph_chunks(text: str, max_chars: int = 2200, overlap_paragraphs: int = 1) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks = []
    current = []
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


def build_actor_maps(all_actors: list[dict], pdf_name: str):
    canonical_names = []
    alias_to_canonical = {}

    for actor in all_actors:
        if actor.get("source_document") != pdf_name:
            continue

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


def get_present_actors(chunk: str, alias_to_canonical: dict[str, str]) -> list[str]:
    chunk_norm = normalize_name(chunk)
    present = []

    for alias_key, canonical in sorted(alias_to_canonical.items(), key=lambda x: len(x[0]), reverse=True):
        if alias_key and alias_key in chunk_norm:
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
    pdf_name: str,
    page_num: int,
    chunk: str,
    actor_names: list[str],
) -> str:
    actor_list_text = "\n".join(f"- {name}" for name in actor_names)

    prompt = f"""
    You extract explicit interactions between known actors in quantum/deep-tech ecosystem texts.

    SOURCE
    source_document: "{pdf_name}"
    page: {page_num}

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
    "source_document": "{pdf_name}",
    "page": {page_num}
    }}

    RULES
    - Use only actors from the KNOWN ACTORS list.
    - Both source_actor and target_actor must be copied exactly from KNOWN ACTORS.
    - Do not invent actors.
    - Do not classify the interaction type.
    - Extract only explicit interactions or co-participation.
    - Interaction can include collaboration, funding, founding, spin-off, donation, establishment, request, cooperation, affiliation, joint development, membership, ownership, controlling shareholder, or participation in the same named initiative.
    - For list-based collaboration, extract pairwise interactions only when the sentence clearly says the listed actors collaborated, jointly built, jointly developed, or jointly established something.
    - Do not create an interaction just because two actors are mentioned in the same document.
    - interaction_phrase must be copied from the text.
    - occurrence_sentence must be the full sentence containing the interaction.
    - If there are no interactions, return [].
    - Return only JSON. No markdown. No explanation.
    """

    response = await acompletion(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
        num_predict=700,
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


async def main():
    actors = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    all_interactions = []

    for pdf_path in PDF_DIR.glob("*.pdf"):
        pdf_name = pdf_path.name
        print(f"Processing interactions for: {pdf_name}")

        actor_names, alias_to_canonical = build_actor_maps(actors, pdf_name)

        if not actor_names:
            print(f"No valid actors found for {pdf_name}, skipping.")
            continue

        valid_actor_keys = {normalize_name(name) for name in actor_names}
        pages = extract_pdf_text(pdf_path)

        for page in pages:
            page_num = page["page"]
            chunks = paragraph_chunks(page["text"], max_chars=900, overlap_paragraphs=0)

            for chunk in chunks:
                present_actors = get_present_actors(chunk, alias_to_canonical)

                if len(present_actors) > 12:
                    present_actors = present_actors[:12]

                if len(present_actors) < 2:
                    continue

                print(f"Extracting interactions: {pdf_name}, page {page_num}")

                try:
                    raw = await extract_interactions_from_chunk(
                        pdf_name=pdf_name,
                        page_num=page_num,
                        chunk=chunk,
                        actor_names=present_actors,
                    )
                except Exception as e:
                    print(f"Model call failed on {pdf_name}, page {page_num}: {e}")
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

    all_interactions = dedupe_edges(all_interactions)

    OUTPUT_EDGES_JSON.write_text(
        json.dumps(all_interactions, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {OUTPUT_EDGES_JSON} with {len(all_interactions)} extracted interactions.")


if __name__ == "__main__":
    asyncio.run(main())