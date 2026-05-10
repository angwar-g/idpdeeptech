# like feed.py but for PDFs (doesn't require a crawled output first)

import json
import os
from dotenv import load_dotenv
load_dotenv()

import re
import asyncio
import warnings
from pathlib import Path

import fitz  # type: ignore PyMuPDF
from litellm import acompletion

warnings.filterwarnings("ignore", category=UserWarning)

PDF_DIR = Path("pdf_input")
PDF_DIR.mkdir(exist_ok=True)

KEYWORDS = [
    "china", "japan", "united states", "european union", "eu",
    "ministry", "agency", "national", "military", "defense",
    "science", "technology", "university", "academy", "institute"
]

def extract_pdf_text(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []

    for page_num, page in enumerate(doc, start=1): # type: ignore
        text = page.get_text("text")
        pages.append({
            "page": page_num,
            "text": text
        })

    return pages

# bigger model, smaller text chunks (?)
def paragraph_chunks(markdown, max_chars=2500, overlap_paragraphs=1):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]
    chunks = []
    current = []
    current_len = 0

    for p in paragraphs:
        if current_len + len(p) > max_chars and current:
            chunks.append("\n\n".join(current))

            # keep last paragraph for context
            current = current[-overlap_paragraphs:] if overlap_paragraphs > 0 else []
            current_len = sum(len(x) for x in current)

        current.append(p)
        current_len += len(p)

    if current:
        chunks.append("\n\n".join(current))

    return chunks

def is_relevant(chunk):
    return any(k in chunk.lower() for k in KEYWORDS)

def clean_json(raw):
    raw = raw.strip()
    raw = raw.replace("```json", "").replace("```", "").strip()

    start = raw.find("[")
    end = raw.rfind("]")

    if start != -1 and end != -1:
        return raw[start:end + 1]

    return raw

def dedupe_results(results):
    seen = set()
    deduped = []

    for r in results:
        if not isinstance(r, dict):
            continue

        key = (
            r.get("canonical_mention", r.get("entity", "")).lower().strip(),
            r.get("source_document", "").lower().strip(),
            str(r.get("page", "")).strip(),
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(r)

    return deduped

async def extract_chunk(pdf_name, page_num, chunk):
    prompt = f"""
    You are extracting named actors from quantum and deep-tech ecosystem texts.

    SOURCE
    source_type: "pdf"
    source_document: "{pdf_name}"
    page: {page_num}

    TEXT
    {chunk}

    TASK
    Extract all explicitly named actors and actor-like entity mentions from the text.

    Return only a valid JSON array. Each object must have exactly these fields:

    {{
    "entity": "...",
    "status": "entity | not_actor | not_specific | uncertain",
    "excluded_reason": "Null | not_actor | not_specific | program_or_initiative | technology | event | location_only | generic_group | bad_extraction | insufficient_context | other",

    "category": "universities | research institutes | vocational training institutions | small and medium-sized enterprises | large enterprises | corporate labs | national government institutions | sub-national government institutions | supranational government institutions | media and cultural institutions | user communities | non-governmental and non-profit organizations | joint research centers | business support institutions | financial support institutions | entrepreneurial scientist | innovation organizer | country | other | unknown | Null",

    "role_in_text": "...",
    "technology_area": "quantum computing | quantum communication | quantum sensing | quantum materials | quantum simulation | quantum cryptography | semiconductors | photonics | AI | robotics | fusion | deep tech general | other | unknown",

    "occurrence_sentence": "...",

    "mentioned_interacting_actors": [
        {{
        "actor_name": "...",
        "interaction_evidence": "exact quote showing interaction or co-participation"
        }}
    ],

    "source_document": "{pdf_name}",
    "page": {page_num},
    "confidence": "high | medium | low"
    }}

    WHAT COUNTS AS AN ACTOR
    Extract explicitly named:
    - universities and higher education institutions
    - research institutes, labs, research centres
    - companies, startups, SMEs, large corporations, corporate labs
    - ministries, agencies, national/regional/local government bodies
    - supranational bodies and international organisations
    - business support institutions, clusters, incubators, accelerators, technology transfer offices
    - financial support institutions, venture capital firms, funds, angel networks
    - NGOs, foundations, civil society organisations, media/cultural institutions, user communities
    - named individuals
    - countries only when they act as policy, geopolitical, funding, strategic, or ecosystem actors

    NON-ACTORS
    If a named mention is not really an actor, still include it only when it looks actor-like, but mark it correctly:
    - strategies, programmes, projects, grants, funds, missions, laws, events → status "not_actor"
    - vague groups like "companies", "researchers", "stakeholders" → status "not_specific"
    - technologies, products, infrastructures, methods → status "not_actor"
    - broken OCR fragments → status "not_actor", excluded_reason "bad_extraction"

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

    INTERACTION FIELD RULES
    - mentioned_interacting_actors should list other named actors in the same sentence or local context that the current actor directly interacts with.
    - Include only explicit interaction or co-participation evidence.
    - Do not classify the interaction type yet.
    - If no interaction is stated, use an empty list [].
    - Do not invent actors.

    GENERAL RULES
    - Use only the provided text.
    - Do not infer websites, countries, abbreviations, or affiliations unless explicitly stated.
    - evidence_quote must be copied exactly from the text.
    - occurrence_sentence must be the full sentence containing the actor.
    - canonical_mention should normalize casing and remove footnote numbers.
    - Use "Null" where a field does not apply.
    - Return [] only if there are no named actors or actor-like mentions.
    - Return only JSON. No markdown. No explanation.
    """

    response = await acompletion(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )
    
    #response = await acompletion(
    #    model="gemini/gemini-2.0-flash",
    #    api_key=os.getenv("GEMINI_API_KEY"),
    #    messages=[{"role": "user", "content": prompt}],
    #    temperature=0
    #)

    return response.choices[0].message.content # type: ignore

async def main():
    all_results = []

    for pdf_path in PDF_DIR.glob("*.pdf"):
        print(f"Reading PDF: {pdf_path.name}")
        pages = extract_pdf_text(pdf_path)

        for page in pages:
            page_num = page["page"]
            text = page["text"]

            # depends on the model's size
            chunks = paragraph_chunks(
                text,
                max_chars=2000,
                overlap_paragraphs=1
            )

            for chunk in chunks:
                if not is_relevant(chunk):
                    continue

                print(f"Extracting: {pdf_path.name}, page {page_num}")
                print("CHUNK PREVIEW:")
                print(chunk[:1000])

                raw = await extract_chunk(pdf_path.name, page_num, chunk)
                await asyncio.sleep(2) # be nice to the API

                print("RAW:", raw[:800]) # type: ignore

                try:
                    parsed = json.loads(clean_json(raw))
                    all_results.extend(parsed)
                except Exception:
                    print("Could not parse JSON:")
                    print(raw[:1000]) # type: ignore

    all_results = dedupe_results(all_results)

    Path("actor_results_pdf.json").write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Saved actor_results_pdf.json with {len(all_results)} unique actors")

if __name__ == "__main__":
    asyncio.run(main())
