import json
import re
from pathlib import Path

INPUT_JSON = Path("3_interaction_results_pdf.json")
OUTPUT_JSON = Path("4_interaction_edges_pdf.json")

ALLOWED_RELATION_LABELS = {
    "technology_transfer",
    "collaborative_leadership",
    "substitution",
    "networking",
    "collaboration_conflict_moderation",
    "no_explicit_relation",
}

REQUIRED_FIELDS = {
    "source_actor",
    "target_actor",
    "interaction_phrase",
    "occurrence_sentence",
    "source_document",
    "page",
}

OPTIONAL_DEFAULTS = {
    "relation_label": "no_explicit_relation",
    "relation_label_confidence": "low",
}

DROP_TEXT_PATTERNS = [
    r"\bby\s+[A-Z][A-Za-z\-]+\s+[A-Z][A-Za-z\-]+\s+and\s+[A-Z][A-Za-z\-]+\s+[A-Z][A-Za-z\-]+\b",
    r"\|\s*\d+\b",
    r"\bthe author would like to thank\b",
    r"\bthis report is produced by\b",
    r"\ball rights reserved\b",
    r"\bviews, positions, and conclusions\b",
    r"\bis a fellow with\b",
    r"\bis a senior adviser\b",
    r"\bis a senior advisor\b",
    r"\bformer intern\b",
]

BAD_PHRASE_PATTERNS = [
    r"^and$",
    r"^or$",
    r"^;+$",
    r"^[,.;:\-–—]+$",
    r"^including$",
    r"^such as$",
    r"\bstands out for\b",
    r"\bfocuses of the fund\b",
    r"\bmentioned establishment\b",
]

VAGUE_GROUP_PATTERNS = [
    r"\busers\b",
    r"\bstakeholders\b",
    r"\bresearchers\b",
    r"\bscientists\b",
    r"\bsuppliers\b",
    r"\bcompanies\b",
    r"\binstitutions\b",
    r"\bother institutions\b",
]

EVIDENCE_PATTERNS = {
    "technology_transfer": [
        r"\bspun off from\b",
        r"\bspin[- ]off\b",
        r"\bcommerciali[sz]",
        r"\bdeployment\b",
        r"\bdeveloped\b",
        r"\bdeveloped by\b",
        r"\bmanufactur",
        r"\bproduced\b",
        r"\bmass produce\b",
        r"\bdonat",
        r"\bequipment\b",
        r"\btechnology transfer\b",
        r"\blicensed\b",
        r"\bpatent\b",
        r"\binfrastructure\b",
    ],
    "collaborative_leadership": [
        r"\bestablished\b",
        r"\bfounded\b",
        r"\bcofounded\b",
        r"\bco-founded\b",
        r"\blaunched\b",
        r"\bfunded\b",
        r"\bfinanced\b",
        r"\bcreated\b",
        r"\bset up\b",
        r"\bformed\b",
        r"\bcoordinat",
        r"\bconven",
        r"\borganis",
        r"\borganiz",
        r"\bguided\b",
        r"\bled by\b",
    ],
    "networking": [
        r"\bin collaboration with\b",
        r"\bcollaboration\b",
        r"\bin cooperation with\b",
        r"\bcooperation\b",
        r"\bjointly\b",
        r"\bpartner",
        r"\bnetwork\b",
        r"\bconsortium\b",
        r"\balliance\b",
        r"\baffiliated with\b",
        r"\bmember of\b",
        r"\bparticipat",
        r"\bcontrolling shareholder\b",
        r"\bowned by\b",
        r"\bacquired\b",
    ],
    "substitution": [
        r"\bsubstitut",
        r"\breplaced\b",
        r"\bfills? the gap\b",
        r"\bstepped in\b",
        r"\btook over\b",
    ],
    "collaboration_conflict_moderation": [
        r"\bmediate\b",
        r"\bmoderate\b",
        r"\bresolved? tensions\b",
        r"\balign(ed|ing)? interests\b",
        r"\bconflict\b",
        r"\bwin-win\b",
    ],
}

LOW_VALUE_SPEECH_ONLY = [
    r"\bsaid\b",
    r"\bstated\b",
    r"\bhas stated\b",
    r"\bnoted\b",
    r"\bmentioned\b",
    r"\baccording to\b",
    r"\breaffirmed\b",
]


def normalize_text(text: str) -> str:
    text = str(text or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_name(name: str) -> str:
    name = normalize_text(name).lower()
    name = re.sub(r"\([^)]*\)", "", name)
    name = re.sub(r"\b(the|a|an)\b", " ", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name.strip()


def combined_text(edge: dict) -> str:
    return normalize_text(
        f"{edge.get('interaction_phrase', '')} {edge.get('occurrence_sentence', '')}"
    )


def has_pattern(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, flags=re.I) for p in patterns)


def clean_edge_fields(edge: dict) -> dict:
    edge = dict(edge)

    for field in [
        "source_actor",
        "target_actor",
        "interaction_phrase",
        "occurrence_sentence",
        "source_document",
    ]:
        edge[field] = normalize_text(edge.get(field, ""))

    for field, default in OPTIONAL_DEFAULTS.items():
        edge[field] = normalize_text(edge.get(field, default)) or default

    try:
        edge["page"] = int(edge.get("page")) # type: ignore
    except Exception:
        edge["page"] = edge.get("page")

    edge["relation_label"] = edge["relation_label"].strip()
    edge["relation_label_confidence"] = edge["relation_label_confidence"].lower().strip()

    if edge["relation_label"] not in ALLOWED_RELATION_LABELS:
        edge["relation_label"] = "no_explicit_relation"

    if edge["relation_label_confidence"] not in {"high", "medium", "low"}:
        edge["relation_label_confidence"] = "low"

    return edge


def schema_ok(edge: dict) -> bool:
    if not REQUIRED_FIELDS.issubset(edge.keys()):
        return False

    for field in REQUIRED_FIELDS:
        if edge.get(field) in [None, ""]:
            return False

    return True


def actors_ok(edge: dict) -> bool:
    source = normalize_name(edge.get("source_actor", ""))
    target = normalize_name(edge.get("target_actor", ""))

    if not source or not target:
        return False

    if source == target:
        return False

    return True


def malformed_ok(edge: dict) -> bool:
    if any('"' in str(k) or str(k).endswith(".") for k in edge.keys()):
        return False

    phrase = normalize_text(edge.get("interaction_phrase", ""))
    sentence = normalize_text(edge.get("occurrence_sentence", ""))

    if len(sentence.split()) < 5:
        return False

    if len(phrase) < 2:
        return False

    if sentence.count("{") or sentence.count("}"):
        return False

    return True


def has_real_evidence(edge: dict) -> bool:
    text = combined_text(edge)

    return any(
        has_pattern(patterns, text)
        for patterns in EVIDENCE_PATTERNS.values()
    )

def actors_appear_in_sentence(edge: dict) -> bool:
    sentence = normalize_text(edge.get("occurrence_sentence", "")).lower()

    source = normalize_text(edge.get("source_actor", "")).lower()
    target = normalize_text(edge.get("target_actor", "")).lower()

    source_tokens = [t for t in source.split() if len(t) > 3]
    target_tokens = [t for t in target.split() if len(t) > 3]

    source_match = any(t in sentence for t in source_tokens)
    target_match = any(t in sentence for t in target_tokens)

    return source_match and target_match

def should_drop(edge: dict) -> bool:
    text = combined_text(edge)
    phrase = normalize_text(edge.get("interaction_phrase", ""))

    if not schema_ok(edge):
        return True

    if not actors_ok(edge):
        return True

    if not malformed_ok(edge):
        return True

    if has_pattern(DROP_TEXT_PATTERNS, text):
        return True

    if has_pattern(BAD_PHRASE_PATTERNS, phrase):
        return True

    # Drop vague group edges unless there is a clear relation verb.
    if has_pattern(VAGUE_GROUP_PATTERNS, text) and not has_real_evidence(edge):
        return True

    # Speech-only is not an interaction unless it also contains a stronger action.
    if has_pattern(LOW_VALUE_SPEECH_ONLY, text) and not has_real_evidence(edge):
        return True

    # Do not keep no_explicit_relation as a garbage bucket.
    if edge.get("relation_label") == "no_explicit_relation" and not has_real_evidence(edge):
        return True

    if not actors_appear_in_sentence(edge) and not has_real_evidence(edge):
        return True

    return False


def infer_relation_label(edge: dict) -> dict:
    text = combined_text(edge)

    for label in [
        "collaboration_conflict_moderation",
        "substitution",
        "networking",
        "collaborative_leadership",
        "technology_transfer",
    ]:
        if has_pattern(EVIDENCE_PATTERNS[label], text):
            edge["relation_label"] = label
            edge["relation_label_confidence"] = "high"
            return edge

    edge["relation_label"] = "no_explicit_relation"
    edge["relation_label_confidence"] = "low"
    return edge


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

def clean_phrase(text: str) -> str:
    text = normalize_text(text)

    text = re.sub(r"\s+,", ",", text)
    text = re.sub(r",\s*$", "", text)

    if len(text.split()) > 12:
        text = " ".join(text.split()[:12])

    return text

def main():
    raw_edges = json.loads(INPUT_JSON.read_text(encoding="utf-8"))

    cleaned = []

    for edge in raw_edges:
        if not isinstance(edge, dict):
            continue

        edge = clean_edge_fields(edge)

        edge["interaction_phrase"] = clean_phrase(edge.get("interaction_phrase", ""))
        edge["occurrence_sentence"] = normalize_text(edge.get("occurrence_sentence", ""))

        if should_drop(edge):
            continue

        edge = infer_relation_label(edge)

        if should_drop(edge):
            continue

        cleaned.append(edge)

    cleaned = dedupe_edges(cleaned)

    OUTPUT_JSON.write_text(
        json.dumps(cleaned, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Saved {OUTPUT_JSON} with {len(cleaned)} cleaned interactions.")


if __name__ == "__main__":
    main()