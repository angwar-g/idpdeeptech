"""Extract named actors from crawled website pages.

Parallel to feed_pdf.py. Reads markdown from crawl_output/*.json files.
Uses the same LLM prompt as feed_pdf.py; only the SOURCE block differs
(source_type=website, source_document=URL).

Writes to 1_actor_results.json so the existing clean_actors.py picks it up
without modification. Saves incrementally after each URL.
"""

import json
import re
import asyncio
import warnings
from pathlib import Path

from dotenv import load_dotenv
from litellm import acompletion
from json_repair import repair_json

load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings:*")


CRAWL_DIR = Path("crawl_output")
OUTPUT_JSON = Path("1_actor_results.json")


def split_oversized_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """Cut a single oversized paragraph into pieces no larger than max_chars.

    Tries sentence boundaries first, then falls back to hard char-boundary splits.
    Used to tame web-markdown blobs (nav bars, footer link soup) that arrive as
    one giant "paragraph" with no internal blank lines.
    """
    if len(paragraph) <= max_chars:
        return [paragraph]

    # Try splitting on sentence-ish boundaries.
    sentences = re.split(r"(?<=[.!?])\s+", paragraph)

    pieces: list[str] = []
    buf = ""

    for sentence in sentences:
        if len(sentence) > max_chars:
            # Even one sentence is too big — flush buf, then hard-split sentence.
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

    # Pre-split any paragraph that is on its own larger than max_chars.
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


def clean_json(raw: str) -> str:
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    # Do NOT replace curly double quotes. They may appear inside evidence text.
    raw = raw.replace("‘", "'").replace("’", "'")

    start = raw.find("[")
    end = raw.rfind("]")

    if start != -1 and end != -1:
        return raw[start:end + 1]

    return raw


async def extract_chunk(source_url: str, chunk_idx: int, chunk: str) -> str:
    """Same prompt as feed_pdf.py, with source_type='website'."""
    prompt = f"""
    You are extracting named actors from quantum and deep-tech ecosystem texts.

    SOURCE
    source_type: "website"
    source_document: "{source_url}"
    page: {chunk_idx}

    TEXT
    {chunk}

    TASK
    Extract explicitly named actors and actor-like entity mentions from the text.

    Return only a valid JSON array. Each object must have exactly these fields:

    {{
    "entity": "...",
    "status": "entity | not_actor | not_specific | uncertain",
    "excluded_reason": "Null | not_actor | not_specific | program_or_initiative | technology | event | location_only | generic_group | bad_extraction | insufficient_context | other",
    "category": "universities | research institutes | vocational training institutions | small and medium-sized enterprises | large enterprises | corporate labs | national government institutions | sub-national government institutions | supranational government institutions | media and cultural institutions | user communities | non-governmental and non-profit organizations | joint research centers | business support institutions | financial support institutions | entrepreneurial scientist | innovation organizer | individual | country | other | unknown | Null",
    "role_in_text": "...",
    "technology_area": "quantum computing | quantum communication | quantum sensing | quantum materials | quantum simulation | quantum cryptography | semiconductors | photonics | AI | robotics | fusion | deep tech general | other | unknown",
    "occurrence_sentence": "...",
    "source_document": "{source_url}",
    "page": {chunk_idx},
    "confidence": "high | medium | low"
    }}

    WHAT COUNTS AS AN ACTOR
    Extract explicitly named:
    - universities and higher education institutions
    - research institutes, laboratories, centres, research groups
    - companies, startups, SMEs, large corporations, corporate labs
    - ministries, agencies, national/regional/local government bodies
    - supranational bodies and international organisations
    - business support institutions, clusters, incubators, accelerators, technology transfer offices
    - financial support institutions, venture capital firms, funds, angel networks
    - NGOs, foundations, civil society organisations, media/cultural institutions, user communities
    - named individuals
    - countries only when they act as policy, geopolitical, funding, strategic, or ecosystem actors

    NON-ACTORS
    If a named mention is not really an actor, include it only when it looks actor-like, but mark it correctly:
    - strategies, programmes, projects, grants, missions, laws, events → status "not_actor"
    - vague groups like "companies", "researchers", "stakeholders" → status "not_specific"
    - technologies, products, infrastructures, methods → status "not_actor"
    - broken OCR fragments → status "not_actor", excluded_reason "bad_extraction"
    - locations with no institutional actor role → status "not_actor", excluded_reason "location_only"

    CATEGORY RULES
    - Choose only one category.
    - Use "universities" for higher education institutions.
    - Use "research institutes" for research-primary institutions.
    - Use "small and medium-sized enterprises" for companies described as startups, SMEs, spin-offs, or small firms.
    - Use "large enterprises" for large companies, multinationals, or corporations.
    - Use "corporate labs" only for named R&D divisions/labs of companies.
    - Use "national government institutions" for ministries, national agencies, national councils, and national public authorities.
    - Use "sub-national government institutions" for regional, state, provincial, municipal, or local government bodies.
    - Use "supranational government institutions" for EU-level or transnational public bodies.
    - Use "business support institutions" for clusters, hubs, incubators, accelerators, science parks, technology transfer offices, and ecosystem platforms.
    - Use "financial support institutions" for investors, VC firms, public/private investment funds, angel networks, and seed funds.
    - Use "non-governmental and non-profit organizations" for NGOs, charities, foundations, and non-profit associations.
    - Use "innovation organizer" for named individuals who coordinate actors across academia, industry, government, or civil society.
    - Use "entrepreneurial scientist" for named individuals combining academic research with company founding or commercial innovation.
    - Use "individual" for named people who do not clearly fit entrepreneurial scientist or innovation organizer.

    GENERAL RULES
    - Use only the provided text.
    - Do not invent actors, websites, countries, abbreviations, or affiliations.
    - occurrence_sentence must be copied from the text and must contain the actor.
    - Do not extract page headers, footers, page titles, or repeated author names unless the surrounding sentence describes their role or affiliation.
    - When multiple named actors appear in one sentence, extract each named actor separately even if only one performs the main action.
    - Do not extract combined mentions like "Actor A and Actor B" as one actor. Extract them separately.
    - Use "Null" where a field does not apply.
    - Return [] only if there are no named actors or actor-like mentions.
    - Return only JSON. No markdown. No explanation.
    """

    response = await acompletion(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        messages=[{"role": "user", "content": prompt}],
        temperature=0,
    )

    return response.choices[0].message.content  # type: ignore


async def main():
    all_results = []

    if not CRAWL_DIR.exists():
        print(f"No {CRAWL_DIR}/ directory found. Run crawl_site.py first.")
        return

    crawl_files = sorted(CRAWL_DIR.glob("*.json"))
    total_files = len(crawl_files)

    if not crawl_files:
        print(f"No JSON files in {CRAWL_DIR}/. Nothing to extract.")
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

        print(f"Reading page ({file_idx}/{total_files}): {url}")

        chunks = paragraph_chunks(
            markdown,
            max_chars=1500,
            overlap_paragraphs=1,
        )
        total_chunks = len(chunks)

        for chunk_idx, chunk in enumerate(chunks, start=1):
            print(
                f"Extracting: {url} "
                f"({file_idx}/{total_files}), "
                f"chunk {chunk_idx}/{total_chunks}"
            )

            try:
                raw = await extract_chunk(url, chunk_idx, chunk)
            except Exception as e:
                print(f"Model call failed for {url} chunk {chunk_idx}: {e}")
                continue

            await asyncio.sleep(1)

            try:
                parsed = json.loads(clean_json(raw))
            except Exception:
                try:
                    repaired = repair_json(clean_json(raw))
                    parsed = json.loads(repaired)
                    print("Repaired malformed JSON.")
                except Exception:
                    print("Could not parse JSON:")
                    print(raw[:1000])
                    continue

            for item in parsed:
                if isinstance(item, dict):
                    item["source_document"] = url
                    item["page"] = chunk_idx

            all_results.extend(parsed)

        # Incremental save after each URL so a crash keeps prior work.
        OUTPUT_JSON.write_text(
            json.dumps(all_results, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Final save.
    OUTPUT_JSON.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {OUTPUT_JSON} with {len(all_results)} extracted records")


if __name__ == "__main__":
    asyncio.run(main())
