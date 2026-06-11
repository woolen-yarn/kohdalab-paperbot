import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import math
import re
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
PROFILE_JSON_PATH = INDEX_DIR / "lab_profile.json"
PROFILE_MD_PATH = INDEX_DIR / "lab_profile.md"
CATEGORY_NAMES = ("materials", "methods", "physics", "applications")

AUTHOR_ALIASES = {
    "m. kohda": "Makoto Kohda",
    "makoto kohda": "Makoto Kohda",
    "kohda makoto": "Makoto Kohda",
    "j. nitta": "Junsaku Nitta",
    "junsaku nitta": "Junsaku Nitta",
    "nitta junsaku": "Junsaku Nitta",
    "y. kunihashi": "Yoji Kunihashi",
    "yoji kunihashi": "Yoji Kunihashi",
    "t. taniguchi": "Takashi Taniguchi",
    "takashi taniguchi": "Takashi Taniguchi",
    "k. watanabe": "Kenji Watanabe",
    "kenji watanabe": "Kenji Watanabe",
}

CORE_THEME_RULES = [
    {
        "name": "PSH in III-V quantum wells",
        "materials": [
            "GaAs / AlGaAs / InGaAs quantum wells",
            "III-V semiconductor heterostructures",
        ],
        "methods": [
            "time-resolved Kerr rotation / TRKR",
            "transient spin grating",
            "gate control and electric-field tuning",
        ],
        "physics": [
            "Persistent Spin Helix / PSH",
            "Rashba-Dresselhaus spin-orbit interaction",
            "spin diffusion, lifetime, and relaxation",
        ],
        "applications": ["semiconductor spintronics devices"],
    },
    {
        "name": "Structured-light control of spin textures",
        "required": {
            "methods": ["structured light / spatial light modulator"],
        },
        "materials": [
            "GaAs / AlGaAs / InGaAs quantum wells",
            "III-V semiconductor heterostructures",
        ],
        "methods": [
            "structured light / spatial light modulator",
            "time-resolved Kerr rotation / TRKR",
        ],
        "physics": [
            "Persistent Spin Helix / PSH",
            "spin diffusion, lifetime, and relaxation",
        ],
        "applications": [
            "semiconductor spintronics devices",
            "spin-wave and wave-parallel computing",
        ],
    },
    {
        "name": "Gate-tunable Rashba-Dresselhaus spin-orbit physics",
        "materials": [
            "GaAs / AlGaAs / InGaAs quantum wells",
            "III-V semiconductor heterostructures",
        ],
        "methods": [
            "gate control and electric-field tuning",
            "magnetotransport and weak anti-localization",
            "time-resolved Kerr rotation / TRKR",
        ],
        "physics": [
            "Rashba-Dresselhaus spin-orbit interaction",
            "spin transport and spin interference",
        ],
        "applications": ["semiconductor spintronics devices"],
    },
    {
        "name": "2D semiconductor exciton and valley spin dynamics",
        "materials": [
            "2D transition-metal dichalcogenides",
            "Janus and layered 2D semiconductors",
        ],
        "methods": [
            "optical spectroscopy and photoluminescence",
            "time-resolved Kerr rotation / TRKR",
        ],
        "physics": ["exciton spin and valley dynamics"],
        "applications": [
            "valleytronics and excitonic devices",
            "optoelectronics and photonics",
        ],
    },
    {
        "name": "Layered III-VI and anisotropic 2D optical materials",
        "materials": ["Janus and layered 2D semiconductors"],
        "methods": ["optical spectroscopy and photoluminescence"],
        "physics": [
            "Rashba-Dresselhaus spin-orbit interaction",
            "exciton spin and valley dynamics",
        ],
        "applications": ["optoelectronics and photonics"],
    },
    {
        "name": "van der Waals magnetism and magnonics",
        "materials": ["van der Waals magnets"],
        "methods": [
            "optical spectroscopy and photoluminescence",
            "spin torque and ferromagnetic resonance",
        ],
        "physics": [
            "magnons and spin waves",
            "spin-orbit torque and spin Hall physics",
        ],
        "applications": [
            "spin-wave and wave-parallel computing",
            "quantum materials and quantum technology",
        ],
    },
]

NEGATIVE_PROFILE_TERMS = [
    {"term": "battery", "weight": 2.0, "reason": "energy-storage-only papers are usually peripheral"},
    {"term": "supercapacitor", "weight": 2.0, "reason": "electrochemical storage is usually peripheral"},
    {"term": "catalysis", "weight": 1.5, "reason": "pure catalysis is usually peripheral"},
    {"term": "photocatalysis", "weight": 1.5, "reason": "photocatalysis without spin/optics context is peripheral"},
    {"term": "biomedical", "weight": 1.5, "reason": "biomedical applications are usually outside the core scope"},
]


PROFILE_TERMS = {
    "materials": [
        (
            "GaAs / AlGaAs / InGaAs quantum wells",
            ["gaas", "algaas", "ingaas", "inalas", "quantum well", "2deg"],
        ),
        (
            "III-V semiconductor heterostructures",
            ["iii-v", "iii v", "semiconductor heterostructure", "inp", "inas", "gasb"],
        ),
        (
            "2D transition-metal dichalcogenides",
            ["wse2", "ws2", "mos2", "mose2", "mote2", "tmd", "transition metal dichalcogenide"],
        ),
        (
            "Janus and layered 2D semiconductors",
            ["janus", "wsse", "moses", "sns", "sns2", "gase", "gallium telluride"],
        ),
        (
            "van der Waals magnets",
            ["crsbr", "cri3", "mnpse3", "van der waals magnet", "2d magnet"],
        ),
        (
            "Halide perovskites",
            ["perovskite", "lead halide", "cspbbr3", "mapbi3"],
        ),
        (
            "Ferromagnetic metal multilayers",
            ["co/pt", "fept", "cofeb", "ta/cofeb", "magnetic tunnel", "synthetic antiferromagnet"],
        ),
    ],
    "methods": [
        (
            "time-resolved Kerr rotation / TRKR",
            ["time-resolved kerr", "trkr", "kerr rotation", "kerr microscopy"],
        ),
        (
            "transient spin grating",
            ["transient spin grating", "spin grating", "tsg"],
        ),
        (
            "optical spectroscopy and photoluminescence",
            ["optical spectroscopy", "photoluminescence", "pl", "reflectance", "raman"],
        ),
        (
            "structured light / spatial light modulator",
            ["structured light", "spatial light modulator", "slm", "vector vortex"],
        ),
        (
            "gate control and electric-field tuning",
            ["gate-controlled", "gate control", "electric field", "field-effect", "voltage-induced"],
        ),
        (
            "magnetotransport and weak anti-localization",
            ["magnetotransport", "weak antilocalization", "weak anti-localization", "wal"],
        ),
        (
            "spin torque and ferromagnetic resonance",
            ["spin-orbit torque", "spin orbit torque", "ferromagnetic resonance", "fmr"],
        ),
    ],
    "physics": [
        (
            "Persistent Spin Helix / PSH",
            ["persistent spin helix", "spin helix", "psh", "inverse persistent spin helix"],
        ),
        (
            "Rashba-Dresselhaus spin-orbit interaction",
            ["rashba", "dresselhaus", "spin-orbit interaction", "spin orbit interaction"],
        ),
        (
            "spin diffusion, lifetime, and relaxation",
            ["spin diffusion", "spin lifetime", "spin relaxation", "spin dephasing", "dyakonov", "elliot"],
        ),
        (
            "spin transport and spin interference",
            ["spin transport", "spin interference", "aharonov-casher", "spin precession"],
        ),
        (
            "exciton spin and valley dynamics",
            ["exciton spin", "valley spin", "valley polarization", "trion", "biexciton"],
        ),
        (
            "magnons and spin waves",
            ["magnon", "spin wave", "spin-wave", "magnonic"],
        ),
        (
            "spin-orbit torque and spin Hall physics",
            ["spin hall", "spin-orbit torque", "rashba-edelstein", "spin current"],
        ),
    ],
    "applications": [
        (
            "semiconductor spintronics devices",
            ["spintronics", "spin transistor", "spin fet", "spin field effect", "spin device"],
        ),
        (
            "spin memory and MRAM",
            ["mram", "magnetic memory", "spin memory", "magnetic tunnel junction"],
        ),
        (
            "spin-wave and wave-parallel computing",
            ["wave-parallel", "spin-based wave", "spin wave logic", "magnonic logic"],
        ),
        (
            "valleytronics and excitonic devices",
            ["valleytronics", "valley device", "exciton transport", "trion", "biexciton"],
        ),
        (
            "optoelectronics and photonics",
            ["optoelectronic", "photonic", "photodetector", "nonlinear optical", "light emitting"],
        ),
        (
            "quantum materials and quantum technology",
            ["quantum technology", "quantum material", "quantum information", "topological"],
        ),
    ],
}


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def compact_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def parse_profile_datetime(value: str) -> datetime | None:
    value = compact_whitespace(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def normalize_author_name(name: str) -> str:
    name = compact_whitespace(re.sub(r"<[^>]+>", " ", name or ""))
    if not name:
        return ""
    normalized = name.replace(",", " ")
    normalized = compact_whitespace(normalized).lower()
    return AUTHOR_ALIASES.get(normalized, name)


def parse_json_list(value: str) -> list[str]:
    try:
        items = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(items, list):
        return []
    return [str(item) for item in items if item]


def normalize_text(text: str) -> str:
    return compact_whitespace(text).lower()


def term_pattern(term: str) -> str:
    escaped = re.escape(term.lower()).replace(r"\ ", r"\s+")
    if re.fullmatch(r"[a-z0-9+\-]+", term.lower()) and len(term) <= 5:
        return rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return escaped


def term_count(text: str, terms: list[str]) -> int:
    lowered = normalize_text(text)
    total = 0
    for term in terms:
        total += len(re.findall(term_pattern(term), lowered))
    return total


def load_documents(
    conn: sqlite3.Connection,
    *,
    max_docs: int,
    max_chunks_per_source: int,
) -> list[dict]:
    documents: dict[str, dict] = {}

    try:
        rows = conn.execute(
            """
            SELECT pdf_path, title, abstract, tags_json, authors_json, year, journal,
                   date_added, date_modified
            FROM papers
            WHERE COALESCE(is_duplicate, 0) = 0
            ORDER BY date_added DESC, zotero_key
            LIMIT ?
            """,
            (max_docs,),
        ).fetchall()
    except sqlite3.OperationalError:
        try:
            rows = [
                (*row, "", "")
                for row in conn.execute(
                    """
                    SELECT pdf_path, title, abstract, tags_json, authors_json, year, journal
                    FROM papers
                    WHERE COALESCE(is_duplicate, 0) = 0
                    ORDER BY zotero_key
                    LIMIT ?
                    """,
                    (max_docs,),
                ).fetchall()
            ]
        except sqlite3.OperationalError:
            rows = []

    for pdf_path, title, abstract, tags_json, authors_json, year, journal, date_added, date_modified in rows:
        source = pdf_path or title
        tags = parse_json_list(tags_json)
        authors = [
            normalized for author in parse_json_list(authors_json)
            if (normalized := normalize_author_name(author))
        ]
        text = "\n".join(
            [title or "", abstract or "", " ".join(tags), " ".join(authors), journal or ""]
        )
        documents[source] = {
            "source": source,
            "title": title or source,
            "authors": authors,
            "year": year or "",
            "journal": journal or "",
            "date_added": date_added or "",
            "date_modified": date_modified or "",
            "text": text,
        }

    if max_chunks_per_source <= 0:
        return list(documents.values())

    try:
        chunk_rows = conn.execute(
            """
            SELECT source, text
            FROM chunks
            ORDER BY source, page_start, page_end, rowid
            """
        )
    except sqlite3.OperationalError:
        return list(documents.values())

    counts_by_source: dict[str, int] = {}
    for source, text in chunk_rows:
        if counts_by_source.get(source, 0) >= max_chunks_per_source:
            continue
        if source not in documents:
            if len(documents) >= max_docs:
                continue
            documents[source] = {
                "source": source,
                "title": source,
                "authors": [],
                "year": "",
                "journal": "",
                "date_added": "",
                "date_modified": "",
                "text": "",
            }
        documents[source]["text"] += "\n" + (text or "")
        counts_by_source[source] = counts_by_source.get(source, 0) + 1

    return list(documents.values())


def document_matches(document: dict) -> dict[str, list[dict]]:
    if "matches" in document:
        return document["matches"]

    matches = {}
    for category in CATEGORY_NAMES:
        entries = []
        for label, terms in PROFILE_TERMS[category]:
            count = term_count(document["text"], terms)
            if count > 0:
                entries.append(
                    {
                        "label": label,
                        "hit_count": count,
                        "terms": terms,
                    }
                )
        entries.sort(key=lambda item: item["hit_count"], reverse=True)
        matches[category] = entries
    document["matches"] = matches
    return matches


def labels_for(document: dict, category: str, *, limit: int = 4) -> list[str]:
    return [item["label"] for item in document_matches(document).get(category, [])[:limit]]


def terms_for_label(category: str, label: str) -> list[str]:
    for candidate_label, terms in PROFILE_TERMS.get(category, []):
        if candidate_label == label:
            return terms
    return []


def rank_category(documents: list[dict], category: str) -> list[dict]:
    ranked = []
    for label, terms in PROFILE_TERMS[category]:
        doc_count = 0
        hit_count = 0
        examples = []
        for document in documents:
            count = 0
            for match in document_matches(document).get(category, []):
                if match["label"] == label:
                    count = match["hit_count"]
                    break
            if count <= 0:
                continue
            doc_count += 1
            hit_count += count
            if len(examples) < 3:
                examples.append(document["title"])
        if doc_count:
            ranked.append(
                {
                    "label": label,
                    "document_count": doc_count,
                    "hit_count": hit_count,
                    "examples": examples,
                }
            )
    ranked.sort(key=lambda item: (item["document_count"], item["hit_count"]), reverse=True)
    return ranked


def theme_rule_matches(document: dict, rule: dict) -> bool:
    matches = document_matches(document)
    for category, wanted_labels in rule.get("required", {}).items():
        wanted = set(wanted_labels)
        found = {item["label"] for item in matches.get(category, [])}
        if not wanted & found:
            return False

    required = 0
    passed = 0
    for category in CATEGORY_NAMES:
        wanted = set(rule.get(category, []))
        if not wanted:
            continue
        required += 1
        found = {item["label"] for item in matches.get(category, [])}
        if wanted & found:
            passed += 1
    return required > 0 and passed >= max(2, required - 1)


def terms_for_theme_rule(rule: dict) -> dict[str, list[str]]:
    by_category = {}
    for category in CATEGORY_NAMES:
        terms = []
        for label in rule.get(category, []):
            terms.extend(terms_for_label(category, label))
        if terms:
            by_category[category] = list(dict.fromkeys(terms))
    return by_category


def build_core_themes(documents: list[dict], *, top: int) -> list[dict]:
    themes = []
    for rule in CORE_THEME_RULES:
        examples = []
        doc_count = 0
        for document in documents:
            if not theme_rule_matches(document, rule):
                continue
            doc_count += 1
            if len(examples) < 4:
                examples.append(document["title"])
        if not doc_count:
            continue
        weight = round(min(1.0, 0.35 + math.log1p(doc_count) / 8), 3)
        themes.append(
            {
                "name": rule["name"],
                "weight": weight,
                "document_count": doc_count,
                "materials": rule.get("materials", []),
                "methods": rule.get("methods", []),
                "physics": rule.get("physics", []),
                "applications": rule.get("applications", []),
                "terms_by_category": terms_for_theme_rule(rule),
                "examples": examples,
            }
        )
    themes.sort(key=lambda item: (item["document_count"], item["weight"]), reverse=True)
    return themes[:top]


def build_theme_combinations(documents: list[dict], *, top: int) -> list[dict]:
    counts: Counter[tuple[str, str, str]] = Counter()
    hit_strength: Counter[tuple[str, str, str]] = Counter()
    examples: dict[tuple[str, str, str], list[str]] = {}

    for document in documents:
        materials = document_matches(document).get("materials", [])[:3]
        methods = document_matches(document).get("methods", [])[:3]
        physics = document_matches(document).get("physics", [])[:3]
        if not materials or not physics:
            continue
        for material in materials:
            for method in methods or [{"label": "unspecified method", "hit_count": 0, "terms": []}]:
                for concept in physics:
                    key = (material["label"], method["label"], concept["label"])
                    counts[key] += 1
                    hit_strength[key] += material["hit_count"] + method["hit_count"] + concept["hit_count"]
                    examples.setdefault(key, [])
                    if len(examples[key]) < 3:
                        examples[key].append(document["title"])

    combinations = []
    for (material, method, physics), doc_count in counts.items():
        if doc_count < 2:
            continue
        weight = round(min(4.0, 1.0 + math.log1p(doc_count) + hit_strength[(material, method, physics)] / 250), 2)
        combinations.append(
            {
                "name": f"{material} × {method} × {physics}",
                "weight": weight,
                "document_count": doc_count,
                "hit_count": int(hit_strength[(material, method, physics)]),
                "materials": [material],
                "methods": [] if method == "unspecified method" else [method],
                "physics": [physics],
                "terms_by_category": {
                    "materials": terms_for_label("materials", material),
                    "methods": terms_for_label("methods", method),
                    "physics": terms_for_label("physics", physics),
                },
                "examples": examples.get((material, method, physics), []),
            }
        )
    combinations.sort(key=lambda item: (item["document_count"], item["hit_count"]), reverse=True)
    return combinations[:top]


def build_hot_topics(documents: list[dict], *, recent_days: int, top: int) -> list[dict]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(30, recent_days))
    recent_docs = []
    for document in documents:
        date = parse_profile_datetime(document.get("date_added", "")) or parse_profile_datetime(
            document.get("date_modified", "")
        )
        if date and date >= cutoff:
            recent_docs.append(document)

    if not recent_docs:
        return []

    all_counts: Counter[tuple[str, str]] = Counter()
    recent_counts: Counter[tuple[str, str]] = Counter()
    examples: dict[tuple[str, str], list[str]] = {}
    for document in documents:
        for category in CATEGORY_NAMES:
            for label in labels_for(document, category, limit=4):
                all_counts[(category, label)] += 1
    for document in recent_docs:
        for category in CATEGORY_NAMES:
            for label in labels_for(document, category, limit=4):
                key = (category, label)
                recent_counts[key] += 1
                examples.setdefault(key, [])
                if len(examples[key]) < 3:
                    examples[key].append(document["title"])

    topics = []
    total_docs = max(1, len(documents))
    total_recent = max(1, len(recent_docs))
    for key, recent_count in recent_counts.items():
        global_count = all_counts[key]
        if recent_count < 2:
            continue
        global_share = global_count / total_docs
        recent_share = recent_count / total_recent
        lift = recent_share / global_share if global_share else 0.0
        trend_score = round(recent_count * min(4.0, lift), 2)
        topics.append(
            {
                "category": key[0],
                "label": key[1],
                "recent_document_count": recent_count,
                "total_document_count": global_count,
                "lift": round(lift, 2),
                "trend": "up" if lift >= 1.25 else "steady",
                "trend_score": trend_score,
                "examples": examples.get(key, []),
            }
        )
    topics.sort(key=lambda item: (item["trend_score"], item["recent_document_count"]), reverse=True)
    return topics[:top]


def build_weighted_terms(categories: dict, core_themes: list[dict], *, top: int) -> list[dict]:
    category_multipliers = {
        "materials": 1.15,
        "methods": 1.25,
        "physics": 1.45,
        "applications": 0.85,
    }
    terms: dict[str, dict] = {}

    for category, multiplier in category_multipliers.items():
        for entry in categories.get(category, []):
            label = entry["label"]
            base = multiplier * (1.0 + math.log1p(entry["document_count"]) / 2.4)
            for term in terms_for_label(category, label):
                key = term.lower()
                weight = round(min(9.5, base), 2)
                current = terms.get(key)
                if not current or weight > current["weight"]:
                    terms[key] = {
                        "term": key,
                        "weight": weight,
                        "category": category,
                        "label": label,
                        "source": "category",
                    }

    for theme in core_themes:
        boost = 0.5 + 1.5 * float(theme.get("weight", 0.0))
        for category_terms in theme.get("terms_by_category", {}).values():
            for term in category_terms:
                key = term.lower()
                current = terms.get(key)
                if not current:
                    terms[key] = {
                        "term": key,
                        "weight": round(min(9.5, boost), 2),
                        "category": "core_theme",
                        "label": theme["name"],
                        "source": "core_theme",
                    }
                else:
                    current["weight"] = round(min(9.5, current["weight"] + boost), 2)
                    current["source"] = "category+core_theme"

    ranked = list(terms.values())
    ranked.sort(key=lambda item: (-item["weight"], item["term"]))
    return ranked[:top]


def rank_metadata(documents: list[dict], field: str, *, top: int) -> list[dict]:
    counts: dict[str, dict] = {}
    for document in documents:
        raw_values = document.get(field, [])
        values = raw_values if isinstance(raw_values, list) else [raw_values]
        seen_in_doc = set()
        for value in values:
            label = compact_whitespace(str(value))
            if not label or label.lower() in {"unknown", "unknown authors"}:
                continue
            key = label.lower()
            if key in seen_in_doc:
                continue
            seen_in_doc.add(key)
            if key not in counts:
                counts[key] = {
                    "label": label,
                    "document_count": 0,
                    "hit_count": 0,
                    "examples": [],
                }
            counts[key]["document_count"] += 1
            counts[key]["hit_count"] += 1
            if len(counts[key]["examples"]) < 3:
                counts[key]["examples"].append(document["title"])

    ranked = list(counts.values())
    ranked.sort(key=lambda item: (-item["document_count"], item["label"].lower()))
    return ranked[:top]


def build_profile(
    documents: list[dict],
    *,
    top: int,
    recent_days: int,
    term_top: int,
    combination_top: int,
) -> dict:
    for document in documents:
        document_matches(document)
    categories = {
        category: rank_category(documents, category)[:top]
        for category in CATEGORY_NAMES
    }
    categories["journals"] = rank_metadata(documents, "journal", top=top)
    categories["authors"] = rank_metadata(documents, "authors", top=top)
    core_themes = build_core_themes(documents, top=top)
    theme_combinations = build_theme_combinations(documents, top=combination_top)
    hot_topics = build_hot_topics(
        documents,
        recent_days=recent_days,
        top=top,
    )
    weighted_terms = build_weighted_terms(
        categories,
        core_themes,
        top=term_top,
    )
    return {
        "generated_at": utc_now(),
        "source": "rag_poc/index/chunks.sqlite3",
        "document_count": len(documents),
        "profile_type": "scoring-ready lab interest profile",
        "categories": categories,
        "core_themes": core_themes,
        "hot_topics": hot_topics,
        "theme_combinations": theme_combinations,
        "weighted_terms": weighted_terms,
        "negative_profile": {
            "terms": NEGATIVE_PROFILE_TERMS,
        },
    }


def profile_to_markdown(profile: dict) -> str:
    lines = [
        "# KohdaLab RAG Profile",
        "",
        f"- generated_at: `{profile['generated_at']}`",
        f"- documents: `{profile['document_count']}`",
        f"- profile_type: `{profile.get('profile_type', 'lab interest profile')}`",
        "",
    ]

    lines.append("## Core Themes / 研究室の核")
    core_themes = profile.get("core_themes", [])
    if not core_themes:
        lines.append("- No matches")
    for theme in core_themes:
        lines.append(
            f"- {theme['name']}: weight={theme['weight']}, "
            f"{theme['document_count']} documents"
        )
        parts = []
        for key, label in (
            ("materials", "materials"),
            ("methods", "methods"),
            ("physics", "physics"),
            ("applications", "applications"),
        ):
            values = theme.get(key, [])
            if values:
                parts.append(f"{label}: {', '.join(values[:3])}")
        if parts:
            lines.append(f"  - {' / '.join(parts)}")
    lines.append("")

    lines.append("## Active Topics / 最近の伸び")
    hot_topics = profile.get("hot_topics", [])
    if not hot_topics:
        lines.append("- No recent-topic signal")
    for topic in hot_topics:
        lines.append(
            f"- {topic['label']} ({topic['category']}): "
            f"{topic['recent_document_count']} recent / {topic['total_document_count']} total, "
            f"lift={topic['lift']}, trend={topic['trend']}"
        )
    lines.append("")

    lines.append("## Theme Combinations / 材料 × 手法 × 物理")
    combinations = profile.get("theme_combinations", [])
    if not combinations:
        lines.append("- No combinations")
    for combo in combinations[:12]:
        lines.append(
            f"- {combo['name']}: weight={combo['weight']}, "
            f"{combo['document_count']} documents"
        )
    lines.append("")

    lines.append("## Weighted Terms / Paper Watch用重み")
    weighted_terms = profile.get("weighted_terms", [])
    if not weighted_terms:
        lines.append("- No weighted terms")
    for entry in weighted_terms[:24]:
        lines.append(
            f"- {entry['term']}: weight={entry['weight']}, "
            f"{entry['category']} / {entry['label']}"
        )
    lines.append("")

    negative_terms = profile.get("negative_profile", {}).get("terms", [])
    lines.append("## Negative Profile / ノイズ抑制")
    if not negative_terms:
        lines.append("- No negative terms")
    for entry in negative_terms:
        lines.append(f"- {entry['term']}: penalty={entry['weight']} ({entry['reason']})")
    lines.append("")

    headings = {
        "materials": "Materials / 材料系",
        "methods": "Methods / 手法",
        "physics": "Physics / 物理",
        "applications": "Applications / 応用",
        "journals": "Journals / 掲載誌",
        "authors": "Authors / 著者",
    }
    for category, heading in headings.items():
        lines.append(f"## {heading}")
        entries = profile["categories"].get(category, [])
        if not entries:
            lines.append("- No matches")
        for entry in entries:
            lines.append(
                f"- {entry['label']}: {entry['document_count']} documents, "
                f"{entry['hit_count']} hits"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a lab-interest profile from the RAG SQLite index.")
    parser.add_argument("--db", type=Path, default=INDEX_DB_PATH)
    parser.add_argument("--json", type=Path, default=PROFILE_JSON_PATH)
    parser.add_argument("--markdown", type=Path, default=PROFILE_MD_PATH)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--max-docs", type=int, default=5000)
    parser.add_argument("--max-chunks-per-source", type=int, default=2)
    parser.add_argument("--recent-days", type=int, default=365)
    parser.add_argument("--term-top", type=int, default=80)
    parser.add_argument("--combination-top", type=int, default=20)
    parser.add_argument("--print", action="store_true", help="Print the markdown profile to stdout.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.db.exists():
        raise SystemExit(f"SQLite index not found: {args.db}")

    conn = sqlite3.connect(args.db)
    try:
        documents = load_documents(
            conn,
            max_docs=args.max_docs,
            max_chunks_per_source=args.max_chunks_per_source,
        )
    finally:
        conn.close()

    profile = build_profile(
        documents,
        top=args.top,
        recent_days=args.recent_days,
        term_top=args.term_top,
        combination_top=args.combination_top,
    )
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.write_text(profile_to_markdown(profile), encoding="utf-8")

    print(f"Wrote {args.json}")
    print(f"Wrote {args.markdown}")
    if args.print:
        print()
        print(profile_to_markdown(profile))


if __name__ == "__main__":
    main()
