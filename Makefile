.PHONY: bot ingest ask bench check

MODELS ?= qwen3:8b gemma4:12b gpt-oss:20b

bot:
	uv run python bot.py

ingest:
	uv run python rag_poc/ingest.py

ask:
	uv run python rag_poc/ask.py "$(Q)"

bench:
	uv run python scripts/benchmark_ollama_models.py --models $(MODELS)

check:
	uv run python -m py_compile bot.py rag_poc/ask.py rag_poc/ingest.py rag_poc/ollama_client.py scripts/benchmark_ollama_models.py
