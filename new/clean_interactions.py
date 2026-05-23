import json
import re
from pathlib import Path

#PLACEHOLDER
INPUT_JSON = Path("./site_outputs/psiquantum_com/3_interaction_results.json")
OUTPUT_JSON = Path("./site_outputs/psiquantum_com/4_edges.json")

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
    r"\b©\b",
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
        r"\bcommercial deployment\b",
        r"\bdelivered to\b",
        r"\bexport to\b",
        r"\bcloud platform\b",
        r"\bapplication development\b",
        r"\bmarket-ready\b",
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
        r"\bannounced\b",
        r"\bsigned investment intent agreements\b",
        r"\bprovided risk capital\b",
        r"\bsupports?\b",
        r"\bprioriti[sz]es\b",
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
        r"\bjointly announced\b",
        r"\bin collaboration with\b",
        r"\btogether\b",
        r"\bco-developed\b",
        r"\bco-built\b",
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

    if "controlling shareholder" in edge["occurrence_sentence"].lower():
        edge["interaction_phrase"] = "becoming the controlling shareholder of " + edge["target_actor"]
        edge["relation_label"] = "networking"

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

# DEPRECATED (I think? ;-;)
def build_short_aliases(actor_nodes):
    short_aliases = set()

    for actor in actor_nodes:
        for alias in actor.get("aliases", []):
            key = normalize_name(alias)
            if 2 <= len(key) <= 6 and key.isalnum():
                short_aliases.add(key)

    return short_aliases

def actor_matches(actor_name, sentence, short_aliases):
    actor_key = normalize_name(actor_name)

    if actor_key in short_aliases:
        return re.search(rf"\b{re.escape(actor_key)}\b", sentence) is not None

    tokens = [t for t in actor_key.split() if len(t) > 3]
    return any(re.search(rf"\b{re.escape(t)}\b", sentence) for t in tokens)

def actors_appear_in_sentence(edge: dict) -> bool:
    return relation_phrase_links_actors(edge)

def actor_in_sentence(actor: str, sentence: str) -> bool:
    actor = normalize_name(actor)

    if not actor:
        return False

    if len(actor) <= 6:
        return re.search(rf"\b{re.escape(actor)}\b", sentence) is not None

    # For multi-word actors, require either full normalized name
    # or at least 2 meaningful tokens.
    if actor in sentence:
        return True

    tokens = [t for t in actor.split() if len(t) > 3]
    if len(tokens) <= 1:
        return any(re.search(rf"\b{re.escape(t)}\b", sentence) for t in tokens)

    matches = sum(
        1 for t in tokens
        if re.search(rf"\b{re.escape(t)}\b", sentence)
    )

    return matches >= 2

def relation_phrase_links_actors(edge: dict) -> bool:
    sentence = normalize_name(edge.get("occurrence_sentence", ""))
    source = edge.get("source_actor", "")
    target = edge.get("target_actor", "")

    return actor_in_sentence(source, sentence) and actor_in_sentence(target, sentence)

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

    strong_relation = re.search(
        r"\bin collaboration with\b|\bcollaboration between\b|\bcontrolling shareholder\b|\bhas developed\b|\bestablished\b|\bspun off from\b|\bfounded\b",
        text,
        flags=re.I,
    )

    if has_pattern(BAD_PHRASE_PATTERNS, phrase) and not strong_relation:
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

    list_collaboration_edge = re.search(
        r"\bcollaboration between\b|\bincluding:\s*\.\.\.",
        text,
        flags=re.I,
    )

    if not relation_phrase_links_actors(edge) and not list_collaboration_edge:
        return True

    if re.search(r"\bcollaboration between\b", text, flags=re.I):
        edge["relation_label"] = "networking"
        edge["relation_label_confidence"] = "medium"
        return False

    return False


def infer_relation_label(edge: dict) -> dict:
    text = combined_text(edge)

    for label in [
        "collaborative_leadership",
        "technology_transfer",
        "networking",
        "substitution",
        "collaboration_conflict_moderation",
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