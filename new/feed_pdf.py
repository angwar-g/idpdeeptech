# like feed.py but for PDFs (doesn't require a crawled output first)

#INVALID_GENERIC_PATTERNS = [
#    r"^other .*",
#    r".* suppliers$",
#    r".* applications .*",
#    r"^companies$",
#    r"^researchers$",
#    r"^stakeholders$",
#    r"^users$",
#    r"^countries$",
#    r"^institutions$",
#    r"^other institutions$",
#    r"^research personnel$",
#    r"^scientists$",
#]

import json
import re
import asyncio
import warnings
from pathlib import Path

from dotenv import load_dotenv
import fitz  # PyMuPDF
from litellm import acompletion
from json_repair import repair_json

load_dotenv()

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", message="Pydantic serializer warnings:*")


PDF_DIR = Path("pdf_input")
PDF_DIR.mkdir(exist_ok=True)

OUTPUT_JSON = Path("1_actor_results_pdf.json")


def extract_pdf_text(pdf_path: Path) -> list[dict]:
    doc = fitz.open(pdf_path)
    pages = []

    for page_num, page in enumerate(doc, start=1):  # type: ignore
        pages.append({
            "page": page_num,
            "text": page.get_text("text"),
        })

    return pages


def paragraph_chunks(text: str, max_chars=1800, overlap_paragraphs=1) -> list[str]:
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


def normalize_text(value: str) -> str:
    value = str(value).lower().strip()
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_aliases(entity: str) -> list[str]:
    entity = entity.strip()
    aliases = [entity]

    m = re.search(r"^(.*?)\s*\((.*?)\)$", entity)
    if m:
        full = m.group(1).strip()
        abbrev = m.group(2).strip()

        if full:
            aliases.append(full)
        if abbrev:
            aliases.append(abbrev)

    return sorted(set(a for a in aliases if a))


def normalize_status_and_category(r: dict) -> dict:
    status = str(r.get("status", "")).lower().strip()
    category = str(r.get("category", "")).strip().lower()

    if status in {
        "actor",
        "individual",
        "institutional",
        "company",
        "university",
        "research institute",
        "government body",
        "financial support institution",
        "business support institution",
    }:
        r["status"] = "entity"

    if status in {"technology", "location_only"}:
        r["status"] = "not_actor"

    if category.lower() == "individuals":
        r["category"] = "individual"

    if not r.get("excluded_reason"):
        r["excluded_reason"] = "Null"

    return r


def is_clean_actor(r: dict) -> bool:
    status = str(r.get("status", "")).lower().strip()
    category = str(r.get("category", "")).lower().strip()
    entity_key = normalize_text(r.get("entity", ""))

    if status not in {"entity", "actor", "uncertain"}:
        return False

    if category in {"", "null", "unknown"}:
        return False

    if entity_key in {
        "center", "centre", "institute", "university",
        "companies", "researchers", "scientists",
        "stakeholders", "users", "other institutions",
    }:
        return False

    return True


def better_record(existing: dict, candidate: dict) -> dict:
    existing_sentence = str(existing.get("occurrence_sentence", ""))
    candidate_sentence = str(candidate.get("occurrence_sentence", ""))

    if len(candidate_sentence) > len(existing_sentence):
        return candidate

    return existing


def dedupe_results(results: list[dict]) -> list[dict]:
    alias_to_key = {}
    key_to_record = {}

    for r in results:
        if not isinstance(r, dict):
            continue

        r = normalize_status_and_category(r)

        if not is_clean_actor(r):
            continue

        entity = str(r.get("entity", "")).strip()

        if not entity:
            continue

        aliases = extract_aliases(entity)
        alias_keys = [normalize_text(alias) for alias in aliases if normalize_text(alias)]

        if not alias_keys:
            continue

        existing_key = None
        for alias_key in alias_keys:
            if alias_key in alias_to_key:
                existing_key = alias_to_key[alias_key]
                break

        main_key = existing_key or alias_keys[0]

        for alias_key in alias_keys:
            alias_to_key[alias_key] = main_key

        r["canonical_actor_key"] = main_key
        r["aliases"] = aliases
        r["pages"] = [r.get("page")]

        if main_key not in key_to_record:
            key_to_record[main_key] = r
        else:
            existing = key_to_record[main_key]
            chosen = better_record(existing, r)

            pages = sorted(set(existing.get("pages", []) + [r.get("page")]))
            aliases_merged = sorted(set(existing.get("aliases", []) + aliases))

            chosen["canonical_actor_key"] = main_key
            chosen["aliases"] = aliases_merged
            chosen["pages"] = pages

            if chosen.get("category") in {"Null", "unknown", ""} and r.get("category") not in {"Null", "unknown", ""}:
                chosen["category"] = r.get("category")

            key_to_record[main_key] = chosen

    return list(key_to_record.values())

async def extract_chunk(pdf_name: str, page_num: int, chunk: str) -> str:
    prompt = f"""
    You are extracting named actors from quantum and deep-tech ecosystem texts.

    SOURCE
    source_type: "pdf"
    source_document: "{pdf_name}"
    page: {page_num}

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
    "source_document": "{pdf_name}",
    "page": {page_num},
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

    for pdf_path in PDF_DIR.glob("*.pdf"):
        print(f"Reading PDF: {pdf_path.name}")
        pages = extract_pdf_text(pdf_path)

        for page in pages:
            page_num = page["page"]
            text = page["text"]

            chunks = paragraph_chunks(
                text,
                max_chars=2500,
                overlap_paragraphs=1,
            )

            for chunk in chunks:
                print(f"Extracting: {pdf_path.name}, page {page_num}")
                # print("CHUNK PREVIEW:")
                # print(chunk[:800])

                raw = await extract_chunk(pdf_path.name, page_num, chunk)
                await asyncio.sleep(1)

                # print("RAW:", raw[:800])  # type: ignore

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
                        item["source_document"] = pdf_path.name
                        item["page"] = page_num

                all_results.extend(parsed)

    OUTPUT_JSON.write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {OUTPUT_JSON} with {len(all_results)} clean actors")


if __name__ == "__main__":
    asyncio.run(main())