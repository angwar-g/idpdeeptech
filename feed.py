import json
import re
import asyncio
import warnings
from pathlib import Path
from litellm import acompletion

warnings.filterwarnings("ignore", category=UserWarning)

seen_urls = set()

KEYWORDS = [
    "partner", "partnership", "collaboration", "collaborate",
    "university", "government", "investor", "funding",
    "customer", "supplier", "contract", "research",
    "founder", "ceo", "president", "mit", "tokyo",
    "quantum", "photonics", "semiconductor"
]

def paragraph_chunks(markdown, max_chars=2500):
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", markdown) if p.strip()]
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
            r.get("source_url", "").lower().strip(),
            r.get("relationship_to_domain", "").lower().strip()
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(r)

    return deduped

async def extract_chunk(url, chunk):
    prompt = f"""
You are extracting actors and ecosystem relationships for a research project on quantum and deep-tech innovation networks.

Source URL: {url}

Text:
{chunk}

Return only valid JSON array. Each object must have:
{{
  "actor_name": "...",
  "actor_type": "company | startup | university | research institute | government body | investor | individual | other | unknown",
  "helix_category": "industry | academia | government | civil society | unknown",
  "role_in_ecosystem": "...",
  "relationship_to_domain": "...",
  "technology_area": "...",
  "evidence": "exact quote from the text",
  "source_url": "{url}"
}}

Rules:
- Use only actors explicitly named in the text.
- Do not infer relationships.
- Evidence must be copied from the text.
- Important: the domain company itself is an actor. If the text describes the domain company, extract it with relationship_to_domain = "self".
- If no other actors are found, return at least the domain company if it is described in the text.
- If no actors are found at all, return [].
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

    for file in Path("crawl_output").glob("*.json"):
        data = json.loads(file.read_text(encoding="utf-8"))
        url = data["url"]

        if url in seen_urls:
            continue

        seen_urls.add(url)

        markdown = data.get("markdown", "")
        chunks = paragraph_chunks(markdown)

        for chunk in chunks:
            if not is_relevant(chunk):
                continue

            print("Extracting:", url)

            raw = await extract_chunk(url, chunk)
            await asyncio.sleep(1)

            try:
                parsed = json.loads(clean_json(raw))
                all_results.extend(parsed)
            except Exception:
                print("Could not parse JSON:")
                print(raw[:1000]) # type: ignore

    all_results = dedupe_results(all_results)

    Path("actor_results.json").write_text(
        json.dumps(all_results, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )

    print(f"Saved actor_results.json with {len(all_results)} unique actors")

if __name__ == "__main__":
    asyncio.run(main())
