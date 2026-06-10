import json
import math
import os
import re
import sqlite3
import sys
from functools import lru_cache
from pathlib import Path

try:
    from .ollama_client import embed, generate
except ImportError:
    from ollama_client import embed, generate


ROOT = Path(__file__).resolve().parent
INDEX_DB_PATH = ROOT / "index" / "chunks.sqlite3"

CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen3:8b")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
TOP_K = int(os.environ.get("PAPERBOT_TOP_K", "6"))
SHORT_TOP_K = int(os.environ.get("PAPERBOT_SHORT_TOP_K", "3"))
DEEP_TOP_K = int(os.environ.get("PAPERBOT_DEEP_TOP_K", "8"))
MAX_PER_SOURCE = int(os.environ.get("PAPERBOT_MAX_PER_SOURCE", "3"))
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-]*|\d+(?:\.\d+)?")
SHORT_QUESTION_RE = re.compile(
    r"(一文|1文|１文|一言|ひとこと|短く|簡潔|brief|one sentence|in one sentence)",
    re.IGNORECASE,
)
DEEP_QUESTION_RE = re.compile(
    r"(比較|違い|まとめ|歴史|レビュー|網羅|詳しく|詳細|体系|整理|compare|review|history)",
    re.IGNORECASE,
)
CANONICAL_TERMS = (
    "Persistent Spin Helix",
    "PSH",
    "Rashba",
    "Dresselhaus",
    "Rashba-Dresselhaus",
    "spin-orbit interaction",
    "SU(2)",
    "2DEG",
    "TRKR",
    "GaAs",
    "(In,Ga)As",
    "(Al,Ga)As",
    "D'yakonov-Perel'",
    "Koralek",
    "Bernevig",
    "Orenstein",
    "Kohda",
    "Salis",
)

TERM_FIXES = (
    ("ランダウ・ドレッシェル（Rashba）", "Rashba"),
    ("ランダウ・ドレッシェル(Rashba)", "Rashba"),
    ("ランダウ・ドレッシェル", "Rashba"),
    ("線形ドレッシェル（Dresselhaus）", "linear Dresselhaus"),
    ("線形ドレッシェル(Dresselhaus)", "linear Dresselhaus"),
    ("線形ドレッシェル", "linear Dresselhaus"),
    ("ランショー", "Rashba"),
    ("ラシュバ", "Rashba"),
    ("ラシバ", "Rashba"),
    ("ドレスラー", "Dresselhaus"),
    ("ドレッシェル", "Dresselhaus"),
    ("ドレッシェルハウス", "Dresselhaus"),
    ("ドレッセルハウス", "Dresselhaus"),
    ("ドレスルハウス", "Dresselhaus"),
    ("ドレセルハウス", "Dresselhaus"),
    ("スピン・オービタル相互作用", "spin-orbit interaction"),
    ("スピン・オービット相互作用", "spin-orbit interaction"),
    ("スピンオービット相互作用", "spin-orbit interaction"),
    ("スピン・オービタル", "spin-orbit"),
    ("スピン・オービット", "spin-orbit"),
    ("スピンオービット", "spin-orbit"),
    ("永続スピンヘリックス", "Persistent Spin Helix (PSH)"),
    ("持続性スピンヘリックス", "Persistent Spin Helix (PSH)"),
    ("持続スピンヘリックス", "Persistent Spin Helix (PSH)"),
)

CANONICAL_CASE_PATTERNS = (
    (re.compile(r"(?:ランダウ|ランドー)[^、。()\n（）]{0,24}[（(]Rashba[）)]"), "Rashba"),
    (re.compile(r"線形[^、。()\n（）]{0,24}[（(]Dresselhaus[）)]"), "linear Dresselhaus"),
    (re.compile(r"\bpersistent spin helix\b", re.IGNORECASE), "Persistent Spin Helix"),
    (re.compile(r"\bpsh\b", re.IGNORECASE), "PSH"),
    (re.compile(r"\brashba\b", re.IGNORECASE), "Rashba"),
    (re.compile(r"\bdresselhaus\b", re.IGNORECASE), "Dresselhaus"),
    (re.compile(r"(Rashba|Dresselhaus|linear Dresselhaus)(spin[- ]orbit)", re.IGNORECASE), r"\1 \2"),
    (re.compile(r"spin[- ]orbit\s*相互作用", re.IGNORECASE), "spin-orbit interaction"),
    (re.compile(r"\bsu\s*\(\s*2\s*\)", re.IGNORECASE), "SU(2)"),
    (re.compile(r"\b2deg\b", re.IGNORECASE), "2DEG"),
    (re.compile(r"\btrkr\b", re.IGNORECASE), "TRKR"),
    (re.compile(r"\bgaas\b", re.IGNORECASE), "GaAs"),
)


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def tokenize(text: str) -> set[str]:
    return {token.lower() for token in TOKEN_RE.findall(text)}


def lexical_score(question: str, text: str) -> float:
    query_terms = tokenize(question)
    if not query_terms:
        return 0.0

    lowered = text.lower()
    text_terms = tokenize(text)
    overlap = len(query_terms & text_terms) / len(query_terms)

    phrase_bonus = 0.0
    question_lower = question.lower()
    if question_lower and question_lower in lowered:
        phrase_bonus += 0.5
    if "persistent spin helix" in question_lower and "persistent spin helix" in lowered:
        phrase_bonus += 0.4
    if "psh" in query_terms and "persistent spin helix" in lowered:
        phrase_bonus += 0.3

    return min(1.0, overlap + phrase_bonus)


def focus_terms(question: str) -> list[str]:
    lowered = question.lower()
    terms = tokenize(question)
    if "persistent spin helix" in lowered or "psh" in terms:
        return [
            "persistent spin helix",
            "spin helix",
            "spin helices",
            "helical spin",
        ]
    return []


def focus_score(question: str, text: str) -> float:
    lowered = text.lower()
    terms = focus_terms(question)
    if not terms:
        return 0.0
    return 1.0 if any(term in lowered for term in terms) else 0.0


def normalize_technical_terms(answer: str) -> str:
    normalized = answer
    for source, target in TERM_FIXES:
        normalized = normalized.replace(source, target)
    for pattern, replacement in CANONICAL_CASE_PATTERNS:
        normalized = pattern.sub(replacement, normalized)
    return normalized


@lru_cache(maxsize=1)
def load_chunks() -> tuple[dict, ...]:
    if not INDEX_DB_PATH.exists():
        raise FileNotFoundError(
            f"SQLite index not found. Run: python {ROOT / 'ingest.py'}"
        )

    chunks = []
    conn = sqlite3.connect(INDEX_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        metadata_by_source = load_metadata_by_source(conn)
        rows = conn.execute(
            """
            SELECT id, source, sha256, page_start, page_end, text, embedding_json
            FROM chunks
            ORDER BY rowid
            """
        )
        for row in rows:
            metadata = metadata_by_source.get(row["source"], {})
            chunks.append(
                {
                    "id": row["id"],
                    "source": row["source"],
                    "source_label": format_source_label(row["source"], metadata),
                    "paper": metadata,
                    "sha256": row["sha256"],
                    "page_start": row["page_start"],
                    "page_end": row["page_end"],
                    "text": row["text"],
                    "embedding": json.loads(row["embedding_json"]),
                }
            )
    finally:
        conn.close()
    return tuple(chunks)


def load_metadata_by_source(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        rows = conn.execute(
            """
            SELECT
                pdf_path,
                zotero_key,
                title,
                authors_json,
                year,
                journal,
                doi
            FROM papers
            WHERE pdf_path IS NOT NULL
              AND pdf_path != ''
              AND COALESCE(is_duplicate, 0) = 0
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return {}

    metadata = {}
    for row in rows:
        metadata[row["pdf_path"]] = {
            "zotero_key": row["zotero_key"],
            "title": row["title"] or "",
            "authors": parse_authors(row["authors_json"]),
            "year": row["year"] or "",
            "journal": row["journal"] or "",
            "doi": row["doi"] or "",
        }
    return metadata


def parse_authors(authors_json: str) -> list[str]:
    try:
        authors = json.loads(authors_json or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(authors, list):
        return []
    return [str(author) for author in authors if author]


def compact_authors(authors: list[str]) -> str:
    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    return f"{authors[0]} et al."


def format_source_label(source: str, metadata: dict | None = None) -> str:
    metadata = metadata or {}
    title = metadata.get("title", "")
    if not title:
        return source

    parts = [title]
    year = metadata.get("year", "")
    authors = compact_authors(metadata.get("authors", []))
    journal = metadata.get("journal", "")
    if year:
        parts.append(f"({year})")
    if authors:
        parts.append(f"- {authors}")
    if journal:
        parts.append(f"- {journal}")
    return " ".join(parts)


def select_top_k(question: str) -> int:
    if SHORT_QUESTION_RE.search(question):
        return max(1, min(SHORT_TOP_K, TOP_K))
    if DEEP_QUESTION_RE.search(question):
        return max(TOP_K, DEEP_TOP_K)
    return TOP_K


def is_short_question(question: str) -> bool:
    return bool(SHORT_QUESTION_RE.search(question))


def search(question: str, chunks: list[dict], top_k: int | None = None) -> list[dict]:
    limit = top_k or select_top_k(question)
    query_vec = embed(question, EMBED_MODEL)
    scored = []
    for chunk in chunks:
        vector_score = cosine(query_vec, chunk["embedding"])
        keyword_score = lexical_score(question, chunk["text"])
        topic_score = focus_score(question, chunk["text"])
        score = 0.72 * vector_score + 0.18 * keyword_score + 0.10 * topic_score
        scored.append((score, chunk))
    scored.sort(key=lambda item: item[0], reverse=True)

    results = []
    seen_pages = set()
    source_counts = {}
    for score, chunk in scored:
        page_key = (chunk["source"], chunk["page_start"], chunk["page_end"])
        if page_key in seen_pages:
            continue

        source = chunk["source"]
        if source_counts.get(source, 0) >= MAX_PER_SOURCE:
            continue

        item = dict(chunk)
        item["score"] = score
        results.append(item)
        seen_pages.add(page_key)
        source_counts[source] = source_counts.get(source, 0) + 1

        if len(results) >= limit:
            break

    if len(results) < limit:
        for score, chunk in scored:
            page_key = (chunk["source"], chunk["page_start"], chunk["page_end"])
            if page_key in seen_pages:
                continue

            item = dict(chunk)
            item["score"] = score
            results.append(item)
            seen_pages.add(page_key)

            if len(results) >= limit:
                break

    return results


def answer_style_instruction(question: str) -> str:
    if is_short_question(question):
        return (
            "回答は1文だけにしてください。"
            "前置きや箇条書きは不要です。文末に根拠Source IDを付けてください。"
        )
    return (
        "質問に必要な範囲で簡潔に答えてください。"
        "複数の論点がある場合のみ箇条書きを使ってください。"
    )


def build_prompt(question: str, contexts: list[dict]) -> str:
    canonical_terms = ", ".join(CANONICAL_TERMS)
    style_instruction = answer_style_instruction(question)
    context_text = "\n\n".join(
        (
            f"Source S{i}: {ctx['source_label']} pp.{ctx['page_start']}-{ctx['page_end']} "
            f"(score={ctx['score']:.3f})\n{ctx['text']}"
        )
        for i, ctx in enumerate(contexts, start=1)
    )

    return f"""あなたはKohdaLabの研究室PaperBotです。
以下の文献抜粋だけを根拠に、日本語で答えてください。
根拠が足りない場合は「この10本のPDF内では十分な根拠が見つかりません」と言ってください。
回答では、根拠を必ず S1, S2 のようなSource IDで示してください。
PDF本文中に出てくる [12], [27] のような引用番号は、回答の根拠番号として使わないでください。
抜粋に書かれていない論文番号、著者名、材料、応用例を推測で追加しないでください。
{style_instruction}
専門用語・固有名詞は原則として文献抜粋の英語表記を維持してください。
特に次の語は翻訳、カタカナ化、言い換え、誤変換をしないでください:
{canonical_terms}
Rashba, Dresselhaus, SU(2), 2DEG, TRKR, GaAs, PSH などは日本語文中でも英語表記のまま使ってください。
PSHを説明するときは「Rashba and linear Dresselhaus spin-orbit interactions」のように書き、RashbaをLandauなど別語に置き換えないでください。
確信のない専門語、著者名、材料名は日本語に訳さず、原文の英語表記をそのまま使ってください。

質問:
{question}

文献抜粋:
{context_text}

回答:
"""


def build_empty_answer_retry_prompt(question: str, contexts: list[dict]) -> str:
    compact_context = "\n\n".join(
        (
            f"S{i}: {ctx['text'][:1200]}"
        )
        for i, ctx in enumerate(contexts[:3], start=1)
    )
    return f"""次の文献抜粋だけを根拠に、質問へ日本語で1文だけ答えてください。
必ず回答本文を書いてください。空回答は禁止です。
根拠として文末に (S1) のようにSource IDを付けてください。
専門用語 Rashba, Dresselhaus, SU(2), PSH, 2DEG, GaAs は英語表記を維持してください。

質問:
{question}

文献抜粋:
{compact_context}

1文回答:
"""


def fallback_empty_answer() -> str:
    return (
        "検索結果は見つかりましたが、LLMが空の回答を返しました。"
        "下のSourcesを確認するか、別モデルで再試行してください。"
    )


def format_sources(contexts: list[dict]) -> str:
    lines = []
    for i, ctx in enumerate(contexts, start=1):
        source_label = ctx.get("source_label") or ctx["source"]
        source_path = ctx["source"]
        suffix = f" [{source_path}]" if source_label != source_path else ""
        lines.append(
            f"S{i}: {source_label} pp.{ctx['page_start']}-{ctx['page_end']} "
            f"score={ctx['score']:.3f}"
            f"{suffix}"
        )
    return "\n".join(lines)


def format_source_ids(contexts: list[dict]) -> str:
    if not contexts:
        return "No sources."
    return ", ".join(f"S{i}" for i, _ in enumerate(contexts, start=1))


def answer_question(question: str) -> tuple[str, list[dict]]:
    chunks = list(load_chunks())
    contexts = search(question, chunks, select_top_k(question))
    answer = generate(build_prompt(question, contexts), CHAT_MODEL)
    answer = normalize_technical_terms(answer)
    if not answer.strip():
        answer = generate(build_empty_answer_retry_prompt(question, contexts), CHAT_MODEL)
        answer = normalize_technical_terms(answer)
    if not answer.strip():
        answer = fallback_empty_answer()
    return answer, contexts


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python ask.py "質問文"')

    question = " ".join(sys.argv[1:]).strip()
    answer, contexts = answer_question(question)

    print("Top sources:")
    print(format_sources(contexts))
    print()
    print(answer)


if __name__ == "__main__":
    main()
