import json
import re
from pathlib import Path
from difflib import SequenceMatcher

#PLACEHOLDER
INPUT_JSON = Path("./site_outputs/psiquantum_com/1_actor_results.json")
OUTPUT_JSON = Path("./site_outputs/psiquantum_com/2_actor_nodes.json")

ENTITY_LIKE_STATUSES = {
    "entity",
    "actor",
    "country",
    "individual",
    "institutional",
    "university",
    "universities",
    "research institute",
    "research institutes",
    "company",
    "large enterprise",
    "small and medium-sized enterprise",
    "financial support institution",
    "business support institution",
    "national government institutions",
    "sub-national government institutions",
    "supranational government institutions",
    "non-governmental and non-profit organizations",
    "joint_research_center",
}


NON_ENTITY_STATUSES = {
    "not_actor",
    "not_specific",
    "technology",
    "location_only",
}


VALID_CATEGORIES = {
    "universities",
    "research institutes",
    "vocational training institutions",
    "small and medium-sized enterprises",
    "large enterprises",
    "corporate labs",
    "national government institutions",
    "sub-national government institutions",
    "supranational government institutions",
    "media and cultural institutions",
    "user communities",
    "non-governmental and non-profit organizations",
    "joint research centers",
    "business support institutions",
    "financial support institutions",
    "entrepreneurial scientist",
    "innovation organizer",
    "individual",
    "country",
    "other",
}


CATEGORY_PRIORITY = {
    "national government institutions": 100,
    "sub-national government institutions": 95,
    "supranational government institutions": 90,
    "universities": 85,
    "research institutes": 80,
    "large enterprises": 75,
    "small and medium-sized enterprises": 70,
    "corporate labs": 65,
    "financial support institutions": 60,
    "business support institutions": 55,
    "joint research centers": 50,
    "non-governmental and non-profit organizations": 45,
    "entrepreneurial scientist": 40,
    "innovation organizer": 35,
    "individual": 30,
    "country": 25,
    "other": 10,
}


def normalize_text(value: str) -> str:
    value = str(value).lower().strip()
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("“", '"').replace("”", '"')
    value = re.sub(r"^the\s+", "", value)
    value = re.sub(r"^prc'?s\s+", "", value)
    value = re.sub(r"\([^)]*\)", "", value)
    value = re.sub(r"[^a-z0-9\s]", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def display_clean(value: str) -> str:
    value = str(value).strip()
    value = re.sub(r"^The\s+", "", value)
    return value.strip()


def extract_aliases(entity: str) -> list[str]:
    entity = display_clean(entity)
    aliases = {entity}

    # Full Name (ABBR)
    m = re.search(r"^(.*?)\s*\((.*?)\)$", entity)
    if m:
        full = m.group(1).strip()
        abbrev = m.group(2).strip()
        if full:
            aliases.add(full)
        if abbrev:
            aliases.add(abbrev)

    # Possessive institution, e.g. CAS's Innovation Academy...
    possessive = re.sub(r"^([A-Z]{2,})'s\s+", r"\1 ", entity)
    aliases.add(possessive)

    # Co. suffix normalization
    aliases.add(re.sub(r"\s+Co\.$", "", entity).strip())

    return sorted(a for a in aliases if a)


def normalize_status(record: dict) -> str:
    status = str(record.get("status", "")).lower().strip()
    category = str(record.get("category", "")).lower().strip()

    if status in ENTITY_LIKE_STATUSES:
        return "entity"

    if category in VALID_CATEGORIES and category not in {"other"}:
        return "entity"

    if status in NON_ENTITY_STATUSES:
        return status

    return status or "unknown"

GENERIC_FRAGMENT_KEYS = {
    "center", "centre", "institute", "university",
    "laboratory", "lab", "companies", "institutions",
    "researchers", "scientists", "suppliers",
    "users", "stakeholders"
}

COMPOSITE_PATTERNS = [
    r"\b and \b",
    r"\s*&\s*",
    r"\b in collaboration with \b",
]

CATEGORY_OVERRIDES = {
    "state council": "national government institutions",
    "prc state council": "national government institutions",
    "prc s state council": "national government institutions",
    "national development and reform commission": "national government institutions",
    "beijing municipal government": "sub-national government institutions",
}

def normalize_category(record: dict) -> str:
    entity_key = normalize_text(record.get("entity", ""))
    if entity_key in CATEGORY_OVERRIDES:
        return CATEGORY_OVERRIDES[entity_key]

    category = str(record.get("category", "")).strip()

    aliases = {
        "Null": "Null",
        "unknown": "unknown",
        "large enterprise": "large enterprises",
        "small and medium-sized enterprise": "small and medium-sized enterprises",
        "research institute": "research institutes",
        "university": "universities",
        "government body": "national government institutions",
        "financial support institution": "financial support institutions",
        "business support institution": "business support institutions",
        "joint_research_center": "joint research centers",
        "individuals": "individual",
    }

    return aliases.get(category, category)

COMPOSITE_PATTERNS = [
    r"\b and \b",
    r"\s*&\s*",
    r"\b in collaboration with \b",
]

GENERIC_FRAGMENT_KEYS = {
    "center", "centre", "institute", "university",
    "laboratory", "lab", "companies", "institutions",
    "researchers", "scientists", "suppliers"
}

def is_composite_actor(entity: str) -> bool:
    return any(re.search(p, entity, flags=re.I) for p in COMPOSITE_PATTERNS)

def is_generic_fragment(entity: str) -> bool:
    return normalize_text(entity) in GENERIC_FRAGMENT_KEYS

def is_actor_node(record: dict) -> bool:
    status = normalize_status(record)
    category = normalize_category(record).lower().strip()
    entity_raw = str(record.get("entity", "")).strip()
    entity = normalize_text(entity_raw)

    if not entity:
        return False

    if status != "entity":
        return False

    if category in {"", "null", "unknown"}:
        return False

    if entity in GENERIC_FRAGMENT_KEYS:
        return False

    if any(re.search(p, entity_raw, flags=re.I) for p in COMPOSITE_PATTERNS):
        return False

    if entity in {"other institutions", "scientists at baqis"}:
        return False

    PRODUCT_OR_PLATFORM_NAMES = {
        "zuchongzhi cloud",
        "tianyan quantum cloud",
        "origin quantum cloud",
        "origin wukong",
        "zuchongzhi 3 0",
        "zuchongzhi 3 2",
        "tianyan 504",
    }

    if entity in PRODUCT_OR_PLATFORM_NAMES:
        return False

    if "platform" in entity and not re.search(r"\b(company|group|institute|university|center|centre)\b", entity):
        return False

    return True

def entity_similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def should_merge(name_a: str, name_b: str, aliases_a: set[str], aliases_b: set[str]) -> bool:
    keys_a = {normalize_text(x) for x in aliases_a}
    keys_b = {normalize_text(x) for x in aliases_b}

    if keys_a & keys_b:
        return True

    a = normalize_text(name_a)
    b = normalize_text(name_b)

    if not a or not b:
        return False

    # Merge obvious suffix variants.
    suffixes = [
        " co",
        " co ltd",
        " company",
        " technology",
    ]

    for suffix in suffixes:
        if a == b + suffix or b == a + suffix:
            return True

    # Merge abbreviation only if abbreviation is listed as alias.
    if len(a) <= 6 and a in keys_b:
        return True

    if len(b) <= 6 and b in keys_a:
        return True

    # Conservative fuzzy merge for long nearly identical names.
    if len(a) > 12 and len(b) > 12 and entity_similarity(a, b) > 0.93:
        return True

    return False


def choose_best_record(records: list[dict]) -> dict:
    def score(r: dict) -> tuple:
        category = normalize_category(r)
        sentence = str(r.get("occurrence_sentence", ""))
        confidence = str(r.get("confidence", "")).lower()

        confidence_score = {"high": 3, "medium": 2, "low": 1}.get(confidence, 0)

        return (
            CATEGORY_PRIORITY.get(category, 0),
            confidence_score,
            len(sentence),
        )

    return max(records, key=score)

def review_flags(record: dict) -> tuple[bool, str]:
    entity = str(record.get("entity", "")).strip()
    key = normalize_text(entity)

    if re.search(r"\b(and|&)\b", entity, flags=re.IGNORECASE):
        return True, "possible_composite_actor"

    if key in {"center", "centre", "institute", "university"}:
        return True, "generic_fragment"

    if str(record.get("occurrence_sentence", "")).strip().endswith(";"):
        return True, "list_fragment_context"

    return False, ""

def merge_group(records: list[dict]) -> dict:
    best = choose_best_record(records).copy()

    all_aliases = set()
    all_pages = set()
    all_mentions = []

    for r in records:
        entity = display_clean(r.get("entity", ""))
        all_aliases.update(extract_aliases(entity))

        page = r.get("page")
        if page is not None:
            all_pages.add(page)

        all_mentions.append({
            "entity": entity,
            "page": page,
            "role_in_text": r.get("role_in_text", ""),
            "occurrence_sentence": r.get("occurrence_sentence", ""),
            "source_document": r.get("source_document", ""),
        })

    best["entity"] = display_clean(best.get("entity", ""))
    best["status"] = "entity"
    best["category"] = normalize_category(best)
    best["canonical_actor_key"] = normalize_text(best["entity"])
    best["aliases"] = sorted(all_aliases)
    best["pages"] = sorted(all_pages)
    best["mentions"] = all_mentions

    needs_review, review_reason = review_flags(best)
    best["needs_review"] = needs_review
    best["review_reason"] = review_reason

    return best


def canonicalize(records: list[dict]) -> list[dict]:
    candidate_records = []

    for r in records:
        if not isinstance(r, dict):
            continue

        r = r.copy()
        r["status"] = normalize_status(r)
        r["category"] = normalize_category(r)

        if is_actor_node(r):
            candidate_records.append(r)

    groups: list[list[dict]] = []

    for record in candidate_records:
        entity = display_clean(record.get("entity", ""))
        aliases = set(extract_aliases(entity))

        matched_group = None

        for group in groups:
            group_aliases = set()
            group_names = []

            for existing in group:
                existing_name = display_clean(existing.get("entity", ""))
                group_names.append(existing_name)
                group_aliases.update(extract_aliases(existing_name))

            if any(should_merge(entity, group_name, aliases, group_aliases) for group_name in group_names):
                matched_group = group
                break

        if matched_group is None:
            groups.append([record])
        else:
            matched_group.append(record)

    nodes = [merge_group(group) for group in groups]
    nodes.sort(key=lambda x: normalize_text(x.get("entity", "")))

    return nodes


def main():
    records = json.loads(INPUT_JSON.read_text(encoding="utf-8"))
    nodes = canonicalize(records)

    OUTPUT_JSON.write_text(
        json.dumps(nodes, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Loaded {len(records)} extracted records")
    print(f"Saved {OUTPUT_JSON} with {len(nodes)} canonical actor nodes")


if __name__ == "__main__":
    main()