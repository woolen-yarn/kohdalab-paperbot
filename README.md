# KohdaLab PaperBot MVP

Slack DMからMac上のPDF RAGを呼び、RTX PCのOllamaで回答する最小構成です。
PaperBot supports Japanese and English questions, commands, and answers.
PaperBotは日本語・英語の質問、コマンド、回答に対応します。

GitHub:

```text
origin   https://github.com/Kohdalab/kohdalab-paperbot      private
personal https://github.com/woolen-yarn/kohdalab-paperbot   public
```

```text
Slack DM
  -> Mac: bot.py
  -> Mac/NAS: rag_poc/index/chunks.sqlite3
  -> RTX PC: Ollama at 10.32.145.143:11434
  -> Slack reply with short source IDs
```

## Current Roles

```text
Mac /Users/kikuchikeito/projects/llm
  bot.py                 Slack Bot
  rag_poc/ingest.py      PDF extraction, chunking, embedding
  rag_poc/ask.py         RAG search and answer generation
  rag_poc/papers/        PDF input folder
  rag_poc/index/         generated local index

RTX PC
  Ollama
  gpt-oss:20b            chat / answer model
  nomic-embed-text       embedding model
```

## Start Slack Bot

First create a local `.env` file. Do not commit it.

```bash
cd /Users/kikuchikeito/projects/llm
cp .env.example .env
```

Edit `.env` and set the real Slack tokens:

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

Start the bot:

```bash
make bot
```

Keep this terminal running. Stop with `Ctrl+C`.

## Ask From Slack

Open a DM with PaperBot and send a question, for example:

```text
Persistent Spin Helixに関係する内容を教えて
```

The bot should reply in the same language as the question, with an answer and
short `Sources` such as `S1, S2, S3`.
Send `sources` to show the full source list from your previous answer. When a
PDF came from Zotero, source entries use Zotero metadata such as title, year,
first author, and journal instead of only the PDF filename.

In DM, PaperBot replies as normal sequential messages. In channel mentions, it replies in a thread to avoid cluttering the channel.

## Slack Commands

PaperBot understands these commands in DM or mention replies:

```text
help / ヘルプ       Show available commands
model / モデル      Show current Ollama chat/embedding model settings
status / 状態       Show DB counts, Zotero sync state, and Ollama connectivity
sources / 根拠      Show sources from your previous answer in the same DM/channel
recent / 最近       Show recent PDFs on the local papers volume
```

Slash-style text such as `/status` may be intercepted by Slack. Send plain
`status`, `model`, `sources`, or their Japanese aliases instead.

## Add Or Replace PDFs

Put PDFs here:

```text
/Users/kikuchikeito/projects/llm/rag_poc/papers/
```

Then rebuild the index:

```bash
cd /Users/kikuchikeito/projects/llm
make ingest
```

`make ingest` is incremental. Unchanged PDFs are skipped by SHA-256, exact
duplicate PDFs are recorded but not embedded twice, and removed PDFs are removed
from the chunk index.

To force a full PDF chunk rebuild while preserving Zotero metadata:

```bash
make ingest INGEST_ARGS=--rebuild
```

Restart `bot.py` after updating the index, because the bot caches the index in memory.
The RAG index is stored as:

```text
rag_poc/index/chunks.sqlite3
```

The ingest summary is stored as:

```text
rag_poc/index/ingest_report.json
```

If the indexed PDF count is lower than the number of PDF files, check this report.
`zero_text_sources` are PDFs where text extraction produced no chunks, and
`duplicate_sources` are exact duplicate files skipped by SHA-256 hash.

## Test RAG Without Slack

```bash
cd /Users/kikuchikeito/projects/llm
make ask Q="PSHの寿命異方性について教えて"
```

## Sync Zotero Metadata

Set Zotero credentials in `.env`:

```text
ZOTERO_LIBRARY_TYPE=group
ZOTERO_LIBRARY_ID=1234567
ZOTERO_API_KEY=...
ZOTERO_PDF_WORKERS=1
```

Then sync Zotero metadata into SQLite:

```bash
cd /Users/kikuchikeito/projects/llm
make zotero
```

The first normal sync fetches all top-level metadata and stores Zotero's library
version in `zotero_sync_state`. Later normal syncs use Zotero's `since` version
parameter and fetch only changed metadata. This avoids calling the Zotero API for
every paper on every run.

For a connection-only check without writing to SQLite:

```bash
uv run python rag_poc/zotero_sync.py --limit 5 --dry-run
```

Metadata is stored in the `papers` table inside:

```text
rag_poc/index/chunks.sqlite3
```

Zotero sync is idempotent by `zotero_key`. If the same paper is registered as
multiple Zotero items, PaperBot marks later matches as duplicates using DOI when
available, otherwise normalized title plus year. Use the `unique_papers` SQLite
view when you want only representative paper records.

To download attached PDFs for representative papers only:

```bash
make zotero ZOTERO_ARGS="--download-pdfs"
```

On normal incremental runs, PaperBot checks PDF attachments only for new or
changed representative papers. Already downloaded PDFs and known no-PDF items
are skipped without checking child attachments again. Add `--refresh-pdf-metadata`
only when you intentionally want to re-check every Zotero item. The default PDF
worker count is `1` to avoid hitting Zotero too aggressively; increase it
manually only for large first-time imports.

PDFs are saved under:

```text
rag_poc/papers/zotero/
```

PDF download is incremental. Existing files are skipped when the Zotero
attachment metadata and local file match. After downloading PDFs, update the RAG
chunks:

```bash
make ingest
```

For a Zotero-only RAG index, rebuild from the Zotero PDF folder:

```bash
make ingest INGEST_ARGS="--rebuild --source-prefix zotero/"
```

After that first rebuild, regular incremental updates can use:

```bash
make ingest INGEST_ARGS="--source-prefix zotero/"
```

On the NAS, the full Zotero update pipeline is:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/sync_zotero_pipeline.sh
```

It checks Ollama, syncs Zotero metadata, downloads unique PDFs, ingests
`rag_poc/papers/zotero/` incrementally, restarts PaperBot, and prints a count
report. To force a full metadata refresh:

```bash
sudo ZOTERO_ARGS="--all --download-pdfs" ./scripts/sync_zotero_pipeline.sh
```

To re-check every PDF attachment intentionally:

```bash
sudo ZOTERO_ARGS="--download-pdfs --refresh-pdf-metadata" ./scripts/sync_zotero_pipeline.sh
```

To force a first-time Zotero-only rebuild:

```bash
sudo REBUILD=1 ./scripts/sync_zotero_pipeline.sh
```

## Logs

Runtime logs are written here:

```text
/Users/kikuchikeito/projects/llm/logs/paperbot.log
```

The log records Slack surface, user id, chat model, embedding model, duration, answer length, question text, selected sources, commands, and errors. It rotates automatically at about 5 MB.

## DS920+ Deployment

To run PaperBot on the Synology DS920+ and keep RTX PC as the LLM/embedding server, use:

[docs/DS920_DEPLOY.md](docs/DS920_DEPLOY.md)

For GitHub + DS920+ workflow, use:

[docs/GIT_WORKFLOW.md](docs/GIT_WORKFLOW.md)

For NAS-local repo operation with Portainer management, use:

[docs/PORTAINER_DEPLOY.md](docs/PORTAINER_DEPLOY.md)

Deployment files:

```text
Dockerfile
docker-compose.nas.yml
docker-compose.portainer.yml
docker-compose.stack-local.yml
.dockerignore
requirements.txt
scripts/deploy_nas.sh
scripts/reindex_nas.sh
scripts/sync_zotero_pipeline.sh
```

## Check RTX PC Ollama

```bash
curl -s http://10.32.145.143:11434/api/tags | python3 -m json.tool
```

Required models on the RTX PC:

```powershell
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

## Benchmark Ollama Models

Measure generation speed for candidate chat models:

```bash
cd /Users/kikuchikeito/projects/llm
make bench MODELS="qwen3:8b gemma4:12b gpt-oss:20b"
```

The benchmark reports Ollama's `eval_count / eval_duration` as generation tokens/sec.

Direct usage:

```bash
uv run python scripts/benchmark_ollama_models.py \
  --base-url http://10.32.145.143:11434 \
  --models qwen3:8b gemma4:12b gpt-oss:20b \
  --runs 2
```

## Notes

- This is the MVP. It intentionally uses a local SQLite index instead of Postgres/pgvector.
- `rag_poc/ingest.py` removes obvious References pages before indexing to reduce citation-list hallucinations.
- `rag_poc/ask.py` uses a light hybrid score: embedding similarity plus keyword/topic hints.
- Short questions such as "一文で" use fewer retrieved chunks; deeper comparison/review questions use more.
- `.env` and `logs/` are intentionally ignored by Git.
- DS920+ deployment keeps always-on Bot control on NAS and heavy LLM/embedding on RTX PC.
