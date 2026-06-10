.PHONY: bot ingest ask zotero bench check

MODELS ?= qwen3:8b gemma4:12b gpt-oss:20b
INGEST_ARGS ?=
ZOTERO_ARGS ?=

bot:
	uv run python bot.py

ingest:
	uv run python rag_poc/ingest.py $(INGEST_ARGS)

ask:
	uv run python rag_poc/ask.py "$(Q)"

zotero:
	uv run python rag_poc/zotero_sync.py $(ZOTERO_ARGS)

bench:
	uv run python scripts/benchmark_ollama_models.py --models $(MODELS)

check:
	uv run python -m py_compile bot.py rag_poc/ask.py rag_poc/ingest.py rag_poc/ollama_client.py rag_poc/zotero_sync.py scripts/benchmark_ollama_models.py
