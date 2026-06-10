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
  qwen3:14b              Japanese-English translation model
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
For Japanese questions, PaperBot translates the question to English with
`PAPERBOT_TRANSLATION_MODEL`, performs RAG and evidence-grounded answering with
`OLLAMA_CHAT_MODEL`, then translates the English answer back to Japanese. This
keeps the core reasoning in the stronger English model while using a Japanese
model only for language conversion.
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

## Sync Notifications

Create a Slack channel such as `#paperbot-log`, invite PaperBot to the channel,
and set:

```text
SYNC_NOTIFY_CHANNEL=#paperbot-log
SYNC_NOTIFY_MODE=errors_only
```

If Slack returns `channel_not_found`, set `SYNC_NOTIFY_CHANNEL` to the channel
ID instead of the `#paperbot-log` name.

Notification modes:

```text
silent       No sync notifications
errors_only  Notify failures only
changes      Notify failures and successful runs with changed papers/PDFs
verbose      Notify every successful run too
```

Recommended start: `errors_only`. Switch to `changes` if you want to know when
new papers or PDFs were picked up without seeing a message every day.

## Paper Watch

Paper Watch finds recent arXiv, Crossref, and journal RSS papers, scores them against the lab profile, and
posts unposted relevant papers to Slack. Create or choose a channel such as
`#paper`, invite PaperBot, and set:

```text
PAPER_WATCH_CHANNEL=#paper
PAPER_WATCH_SOURCES=arxiv,crossref,rss
PAPER_WATCH_CONTACT_EMAIL=your-email@example.edu
PAPER_WATCH_LOOKBACK_DAYS=14
PAPER_WATCH_MAX_RESULTS=80
PAPER_WATCH_CROSSREF_ROWS=10
PAPER_WATCH_CROSSREF_MAX_QUERIES=2
PAPER_WATCH_CROSSREF_SLEEP_SECONDS=1
PAPER_WATCH_RSS_GROUPS=pr,nature,aip
PAPER_WATCH_RSS_MAX_ITEMS_PER_FEED=20
PAPER_WATCH_RSS_SLEEP_SECONDS=1
PAPER_WATCH_RSS_CROSSREF_FALLBACK=true
PAPER_WATCH_RSS_CROSSREF_FALLBACK_ROWS=10
PAPER_WATCH_POST_LIMIT=5
PAPER_WATCH_MIN_SCORE=6
PAPER_WATCH_BILINGUAL_INTRO=true
PAPER_WATCH_INCLUDE_ABSTRACT=false
PAPER_WATCH_VERBOSE_MESSAGE=false
PAPER_WATCH_SUMMARY_MODEL=gpt-oss:20b
PAPER_WATCH_TRANSLATION_MODEL=qwen3:14b
PAPER_WATCH_TRANSLATION_ENABLED=true
PAPER_WATCH_USE_RAG_SCORE=true
PAPER_WATCH_EMBED_MODEL=nomic-embed-text
PAPER_WATCH_RAG_WEIGHT=8
PAPER_WATCH_RAG_CANDIDATE_LIMIT=30
PAPER_WATCH_RAG_MAX_CHUNKS=1200
PAPER_WATCH_RAG_CHUNKS_PER_SOURCE=2
PAPER_WATCH_RAG_MIN_TERM_SCORE=1
```

RSS sources are grouped so they can be scheduled separately:

```text
pr      Physical Review Letters, Physical Review B, Physical Review Applied
nature  Nature Physics, Nature Communications, Communications Physics
aip     Applied Physics Letters
```

RSS feeds are deliberately small and configurable. Override the built-in list
when a publisher changes a URL:

```text
PAPER_WATCH_RSS_FEEDS=aps_prl|pr|Physical Review Letters|https://feeds.aps.org/rss/recent/prl.xml;nature_physics|nature|Nature Physics|https://www.nature.com/nphys.rss
```

Paper Watch scores papers with both profile terms and the existing lab RAG
index. First it scores title/abstract matches against the research profile.
Then it embeds each candidate abstract and compares it with representative
chunks from the indexed lab PDFs. The final score is:

```text
score = term_score + rag_similarity * PAPER_WATCH_RAG_WEIGHT
```

Paper Watchは、研究プロファイル語による `term_score` と、既存PDF RAG index
との abstract 類似度 `rag_similarity` の両方で候補を選びます。Slack通知には
デフォルトでタイトル、著者、日英紹介、リンク、近かった研究室PDFだけを表示します。
`PAPER_WATCH_VERBOSE_MESSAGE=true` にすると `score`, `term`, `rag` などの詳細も
表示します。`PAPER_WATCH_INCLUDE_ABSTRACT=true` にすると Abstract も表示します。

Crossref and RSS access are deliberately conservative. By default, Paper Watch sends at
most two Crossref queries per run, each with ten rows, and sleeps one second
between Crossref requests. RSS reads at most 20 items per feed and also sleeps
between feed requests. Set `PAPER_WATCH_CONTACT_EMAIL` so Crossref receives
a polite `mailto` parameter and User-Agent. If Crossref returns `429` or another
client-side 4XX response, Paper Watch stops Crossref fetching for that run. If
an RSS feed returns `429` or a 4XX response, only that feed is skipped. AIP/APL
RSS may be blocked by the publisher; when an RSS group has no entries, the
optional fallback sends one conservative Crossref query for that journal group.

The default profile includes topics such as Persistent Spin Helix, Rashba,
Dresselhaus, spin diffusion, TRKR, semiconductor spintronics, 2D magnets,
CrSBr, magnons, exciton spin, WSe2/WS2/MoS2, and optical spectroscopy. Override
with `PAPER_WATCH_TERMS`, for example:

```text
PAPER_WATCH_TERMS=persistent spin helix:9,trkr:6,crsbr:6,magnon:5
```

Each posted paper gets a bilingual EN/JA introduction. The English introduction
is generated by `PAPER_WATCH_SUMMARY_MODEL`, and the Japanese introduction is a
translation by `PAPER_WATCH_TRANSLATION_MODEL`.

Paper Watch also reads a generated lab-interest profile:

```text
rag_poc/index/lab_profile.json
rag_poc/index/lab_profile.md
```

The profile summarizes materials, methods, physics topics, applications,
frequent authors, and journals found in the
indexed Zotero/RAG corpus and is used as hidden context for the bilingual
paper introduction. Generate or refresh it manually with:

```bash
make lab-profile
```

The NAS Zotero sync pipeline refreshes this profile automatically after PDF
ingest.

RAG scoring defaults are intentionally conservative for DS920+. By default,
Paper Watch embeds only the top 30 term-matched candidates and compares them
against up to two representative chunks per indexed PDF, capped at 1200 chunks.
Disable RAG scoring with `PAPER_WATCH_USE_RAG_SCORE=false` or a one-off
`--no-rag-score`.

Run a dry run locally:

```bash
make paper-watch PAPER_WATCH_ARGS=--dry-run
```

Run on the NAS:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/run_paper_watch.sh
```

Recommended DSM scheduled tasks:

```bash
# Weekly arXiv
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources arxiv

# Monthly week 1: APS / Physical Review family
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups pr

# Monthly week 2: Nature family
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups nature

# Monthly week 3: AIP / Applied Physics Letters
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups aip

# Optional monthly broad journal search through Crossref
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources crossref
```

For a single combined DSM scheduled task, use root and run:

```bash
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh
```

If Slack returns `channel_not_found`, set `PAPER_WATCH_CHANNEL` to the channel
ID instead of the `#paper` name.

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

`make ingest` is incremental. Unchanged PDFs are normally skipped by file size
and mtime without reading the PDF body. Use `--verify-hash` when you want to
read unchanged PDFs and verify SHA-256. Exact duplicate PDFs are recorded but
not embedded twice, and removed PDFs are removed from the chunk index.

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

For a slower full integrity check of unchanged PDFs:

```bash
make ingest INGEST_ARGS="--source-prefix zotero/ --verify-hash"
```

On the NAS, the full Zotero update pipeline is:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/sync_zotero_pipeline.sh
```

It checks Ollama, syncs Zotero metadata, downloads unique PDFs, ingests
`rag_poc/papers/zotero/` incrementally, restarts PaperBot, and prints a count
report. It also sends Slack notifications according to `SYNC_NOTIFY_MODE`.
To force a full metadata refresh:

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
scripts/run_paper_watch.sh
```

## Check RTX PC Ollama

```bash
curl -s http://10.32.145.143:11434/api/tags | python3 -m json.tool
```

Required models on the RTX PC:

```powershell
ollama pull gpt-oss:20b
ollama pull qwen3:14b
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
