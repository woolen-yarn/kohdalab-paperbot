import argparse
import json
import re
import sqlite3
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_DIR = ROOT / "index"
INDEX_DB_PATH = INDEX_DIR / "chunks.sqlite3"
PROFILE_JSON_PATH = INDEX_DIR / "lab_profile.json"
PROFILE_MD_PATH = INDEX_DIR / "lab_profile.md"


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
            ["janus", "wsse", "moses", "sns", "sns2", "gase", "gate", "gallium telluride"],
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
            SELECT pdf_path, title, abstract, tags_json, authors_json, year, journal
            FROM papers
            WHERE COALESCE(is_duplicate, 0) = 0
            ORDER BY date_added DESC, zotero_key
            LIMIT ?
            """,
            (max_docs,),
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []

    for pdf_path, title, abstract, tags_json, authors_json, year, journal in rows:
        source = pdf_path or title
        tags = parse_json_list(tags_json)
        authors = parse_json_list(authors_json)
        text = "\n".join(
            [title or "", abstract or "", " ".join(tags), " ".join(authors), journal or ""]
        )
        documents[source] = {
            "source": source,
            "title": title or source,
            "authors": authors,
            "year": year or "",
            "journal": journal or "",
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
                "text": "",
            }
        documents[source]["text"] += "\n" + (text or "")
        counts_by_source[source] = counts_by_source.get(source, 0) + 1

    return list(documents.values())


def rank_category(documents: list[dict], category: str) -> list[dict]:
    ranked = []
    for label, terms in PROFILE_TERMS[category]:
        doc_count = 0
        hit_count = 0
        examples = []
        for document in documents:
            count = term_count(document["text"], terms)
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


def build_profile(documents: list[dict], *, top: int) -> dict:
    categories = {
        category: rank_category(documents, category)[:top]
        for category in ("materials", "methods", "physics", "applications")
    }
    categories["journals"] = rank_metadata(documents, "journal", top=top)
    categories["authors"] = rank_metadata(documents, "authors", top=top)
    return {
        "generated_at": utc_now(),
        "source": "rag_poc/index/chunks.sqlite3",
        "document_count": len(documents),
        "categories": categories,
    }


def profile_to_markdown(profile: dict) -> str:
    lines = [
        "# KohdaLab RAG Profile",
        "",
        f"- generated_at: `{profile['generated_at']}`",
        f"- documents: `{profile['document_count']}`",
        "",
    ]
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

    profile = build_profile(documents, top=args.top)
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
