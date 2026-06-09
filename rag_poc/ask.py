import json
import math
import os
import re
import sys
from functools import lru_cache
from pathlib import Path

try:
    from .ollama_client import embed, generate
except ImportError:
    from ollama_client import embed, generate


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "index" / "chunks.jsonl"

CHAT_MODEL = os.environ.get("OLLAMA_CHAT_MODEL", "qwen3:8b")
EMBED_MODEL = os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")
TOP_K = int(os.environ.get("PAPERBOT_TOP_K", "6"))
MAX_PER_SOURCE = int(os.environ.get("PAPERBOT_MAX_PER_SOURCE", "3"))
TOKEN_RE = re.compile(r"[a-zA-Z][a-zA-Z0-9+\-]*|\d+(?:\.\d+)?")
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
    ("ランショー", "Rashba"),
    ("ラシュバ", "Rashba"),
    ("ラシバ", "Rashba"),
    ("ドレスラー", "Dresselhaus"),
    ("ドレッセルハウス", "Dresselhaus"),
    ("ドレスルハウス", "Dresselhaus"),
    ("ドレセルハウス", "Dresselhaus"),
    ("スピン・オービタル", "spin-orbit"),
    ("スピン・オービット", "spin-orbit"),
    ("スピンオービット", "spin-orbit"),
    ("永続スピンヘリックス", "Persistent Spin Helix (PSH)"),
    ("持続性スピンヘリックス", "Persistent Spin Helix (PSH)"),
    ("持続スピンヘリックス", "Persistent Spin Helix (PSH)"),
)

CANONICAL_CASE_PATTERNS = (
    (re.compile(r"\bpersistent spin helix\b", re.IGNORECASE), "Persistent Spin Helix"),
    (re.compile(r"\bpsh\b", re.IGNORECASE), "PSH"),
    (re.compile(r"\brashba\b", re.IGNORECASE), "Rashba"),
    (re.compile(r"\bdresselhaus\b", re.IGNORECASE), "Dresselhaus"),
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
    if not INDEX_PATH.exists():
        raise FileNotFoundError(f"Index not found. Run: python {ROOT / 'ingest.py'}")

    chunks = []
    with INDEX_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                chunks.append(json.loads(line))
    return tuple(chunks)


def search(question: str, chunks: list[dict]) -> list[dict]:
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

        if len(results) >= TOP_K:
            break

    if len(results) < TOP_K:
        for score, chunk in scored:
            page_key = (chunk["source"], chunk["page_start"], chunk["page_end"])
            if page_key in seen_pages:
                continue

            item = dict(chunk)
            item["score"] = score
            results.append(item)
            seen_pages.add(page_key)

            if len(results) >= TOP_K:
                break

    return results


def build_prompt(question: str, contexts: list[dict]) -> str:
    canonical_terms = ", ".join(CANONICAL_TERMS)
    context_text = "\n\n".join(
        (
            f"Source S{i}: {ctx['source']} pp.{ctx['page_start']}-{ctx['page_end']} "
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
専門用語・固有名詞は原則として文献抜粋の英語表記を維持してください。
特に次の語は翻訳、カタカナ化、言い換え、誤変換をしないでください:
{canonical_terms}
Rashba, Dresselhaus, SU(2), 2DEG, TRKR, GaAs, PSH などは日本語文中でも英語表記のまま使ってください。
確信のない専門語、著者名、材料名は日本語に訳さず、原文の英語表記をそのまま使ってください。

質問:
{question}

文献抜粋:
{context_text}

回答:
"""


def format_sources(contexts: list[dict]) -> str:
    lines = []
    for i, ctx in enumerate(contexts, start=1):
        lines.append(
            f"S{i}: {ctx['source']} pp.{ctx['page_start']}-{ctx['page_end']} "
            f"score={ctx['score']:.3f}"
        )
    return "\n".join(lines)


def answer_question(question: str) -> tuple[str, list[dict]]:
    chunks = list(load_chunks())
    contexts = search(question, chunks)
    answer = generate(build_prompt(question, contexts), CHAT_MODEL)
    answer = normalize_technical_terms(answer)
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
