# feed_pdf.py
import json
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

    for page_num, page in enumerate(doc, start=1):
        text = page.get_text("text")
        pages.append({
            "page": page_num,
            "text": text
        })

    return pages

def paragraph_chunks(text, max_chars=2500):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""

    for p in paragraphs:
        if len(current) + len(p) > max_chars:
            if current:
                chunks.append(current)
            current = p
        else:
            current += "\n\n" + p if current else p

    if current:
        chunks.append(current)

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
            r.get("actor_name", "").lower().strip(),
            r.get("source_document", "").lower().strip(),
            str(r.get("page", "")).strip(),
            r.get("role_in_text", "").lower().strip()
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(r)

    return deduped

async def extract_chunk(pdf_name, page_num, chunk):
    prompt = f"""
    You are extracting named actors from a PDF about quantum technology policy, strategy, research, and innovation ecosystems.

    Source document: {pdf_name}
    Page: {page_num}

    Text:
    {chunk}

    Return only valid JSON array. Each object must have:
    {{
    "actor_name": "...",
    "actor_type": "country | government body | company | university | research institute | individual | international organisation | other | unknown",
    "helix_category": "industry | academia | government | civil society | unknown",
    "role_in_text": "...",
    "technology_area": "...",
    "evidence": "exact quote from the text",
    "source_document": "{pdf_name}",
    "page": {page_num}
    }}

    Extract:
    - countries, e.g. China, Japan, United States
    - government agencies or ministries
    - universities
    - research institutes
    - companies
    - international organisations
    - named individuals

    Rules:
    - Extract actors even if there is no explicit relationship.
    - Evidence must be copied from the text.
    - Do not invent names.
    - Return [] only if the text contains no named actors at all.
    - Return only valid JSON. No markdown. No explanation.
    """

    response = await acompletion(
        model="ollama/qwen2.5:3b",
        api_base="http://localhost:11434",
        messages=[{"role": "user", "content": prompt}],
        temperature=0
    )

    return response.choices[0].message.content # type: ignore

async def main():
    all_results = []

    for pdf_path in PDF_DIR.glob("*.pdf"):
        print(f"Reading PDF: {pdf_path.name}")
        pages = extract_pdf_text(pdf_path)

        for page in pages:
            page_num = page["page"]
            text = page["text"]

            chunks = paragraph_chunks(text)

            for chunk in chunks:
                if not is_relevant(chunk):
                    continue

                print(f"Extracting: {pdf_path.name}, page {page_num}")
                print("CHUNK PREVIEW:")
                print(chunk[:1000])

                raw = await extract_chunk(pdf_path.name, page_num, chunk)
                await asyncio.sleep(1)

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
