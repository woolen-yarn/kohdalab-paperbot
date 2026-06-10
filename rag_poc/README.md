# KohdaLab PaperBot RAG PoC

Mac上でPDF 10本を対象にした最小RAGを動かすためのPoCです。
Questions and answers can be Japanese or English.
質問と回答は日本語・英語どちらにも対応します。

構成:

```text
Mac
├─ rag_poc/papers/       PDFを置く
├─ ingest.py             PDF抽出 + chunk化 + embedding
├─ zotero_sync.py        Zotero metadata sync
├─ ask.py                検索 + Ollama回答
└─ index/chunks.sqlite3  RAG chunks + Zotero metadata

RTX PC
└─ Ollama
   ├─ gpt-oss:20b
   └─ nomic-embed-text
```

## 1. RTX PCでembeddingモデルを入れる

RTX PCのPowerShellで:

```powershell
ollama pull nomic-embed-text
```

`gpt-oss:20b` がまだなら:

```powershell
ollama pull gpt-oss:20b
```

Macから疎通確認:

```bash
curl -s http://10.32.145.143:11434/api/tags | python3 -m json.tool
```

## 2. Mac側のPython環境

このPoCは `/Users/kikuchikeito/projects/llm` の `uv` 環境で動かします。

初回だけ:

```bash
cd /Users/kikuchikeito/projects/llm
uv add pymupdf
```

## 3. PDFを置く

PDFを10本程度ここに入れます:

```text
/Users/kikuchikeito/projects/llm/rag_poc/papers/
```

## 4. index作成

```bash
cd /Users/kikuchikeito/projects/llm
make ingest
```

`make ingest` は差分更新です。既に取り込まれているPDFは通常、file size と mtime が
同じならPDF本文を読まずに高速スキップします。完全な整合性確認をしたい場合だけ
`--verify-hash` を付けると、既存PDFもSHA-256で検証します。
完全重複PDFは記録だけしてembeddingを重複作成しません。

chunkを作り直したい場合:

```bash
make ingest INGEST_ARGS=--rebuild
```

作成されるファイル:

```text
rag_poc/index/chunks.sqlite3
rag_poc/index/ingest_report.json
```

SQLiteの主なテーブル:

```text
chunks  PDF本文chunkとembedding
pdf_documents  PDFごとのsha256/status/chunk_count
papers  Zotero論文メタデータ
unique_papers  重複を除いたZotero論文メタデータview
zotero_sync_state  Zotero差分同期用library version
```

`chunks.source` が `papers.pdf_path` と一致する場合、回答のSourcesはPDFファイル名ではなく
Zotero metadataのtitle/year/author/journalを使って表示されます。

## 5. 質問する

```bash
cd /Users/kikuchikeito/projects/llm
make ask Q="TRKRで300 K測定している論文は？"
```

環境変数でモデルやURLを変えられます:

```bash
export OLLAMA_BASE_URL="http://10.32.145.143:11434"
export OLLAMA_CHAT_MODEL="gpt-oss:20b"
export OLLAMA_EMBED_MODEL="nomic-embed-text"
make ask Q="PSHの寿命異方性について教えて"
```

## Slack Bot連携

トップ階層の `bot.py` はこのRAG処理を呼びます。

```bash
cd /Users/kikuchikeito/projects/llm
make bot
```

PDFを追加して `ingest.py` を再実行した後は、`bot.py` も再起動してください。

## Slack同期通知

`#paperbot-log` のような通知用チャンネルを作り、PaperBotを招待してから `.env` に設定します。

```text
SYNC_NOTIFY_CHANNEL=#paperbot-log
SYNC_NOTIFY_MODE=errors_only
```

`errors_only` は失敗時だけ通知します。`changes` は失敗時に加えて、新規論文/PDFがあった成功同期だけ通知します。
もしSlack側で `channel_not_found` になる場合は、`#paperbot-log` ではなくチャンネルIDを
`SYNC_NOTIFY_CHANNEL` に設定してください。

## Paper Watch

arXiv、Crossref、対象誌RSSから新着論文を拾い、研究室プロファイルに合うものだけ
`#paper` に投稿します。

```text
PAPER_WATCH_CHANNEL=#paper
PAPER_WATCH_SOURCES=arxiv,crossref,rss
PAPER_WATCH_LOOKBACK_DAYS=14
PAPER_WATCH_MAX_RESULTS=80
PAPER_WATCH_RSS_GROUPS=pr,nature,aip
PAPER_WATCH_RSS_CROSSREF_FALLBACK=true
PAPER_WATCH_RSS_CROSSREF_FALLBACK_MAX_JOURNALS=6
PAPER_WATCH_POST_LIMIT=5
PAPER_WATCH_MIN_SCORE=6
PAPER_WATCH_BILINGUAL_INTRO=true
PAPER_WATCH_SUMMARY_MODEL=gpt-oss:20b
PAPER_WATCH_TRANSLATION_MODEL=qwen3:14b
```

Paper Watchは研究室プロファイル語の `term_score` と、候補abstractを既存PDF RAG
indexに照合した `rag_score` を組み合わせて候補を選びます。RSS/Crossref fallbackは
`pr`, `pr_ext`, `nature`, `nature_ext`, `aip`, `nano_2d`, `broad_high` の
groupに分けてあり、Synologyのタスクスケジューラで週次arXiv、月次RSS groupの
ように分散実行できます。投稿文は英語紹介を
`PAPER_WATCH_SUMMARY_MODEL`、日本語化を `PAPER_WATCH_TRANSLATION_MODEL` で生成します。

dry-run:

```bash
make paper-watch PAPER_WATCH_ARGS=--dry-run
```

## Zoteroメタデータ同期

`.env` に `ZOTERO_LIBRARY_ID` と `ZOTERO_API_KEY` を入れてから:

```bash
cd /Users/kikuchikeito/projects/llm
make zotero
```

Zotero同期は `zotero_key` でupsertします。同じ論文が複数itemとして登録されている場合は、
DOIまたは正規化title+yearでduplicate判定し、`duplicate_of` に代表itemを記録します。
初回の通常同期は全top-level metadataを取得し、2回目以降はZotero library versionの
`since` parameterで差分metadataだけを取得します。

代表itemだけ添付PDFを取得する場合:

```bash
make zotero ZOTERO_ARGS="--download-pdfs"
make ingest INGEST_ARGS="--rebuild --source-prefix zotero/"
```

以後の差分更新:

```bash
make zotero ZOTERO_ARGS="--download-pdfs"
make ingest INGEST_ARGS="--source-prefix zotero/"
```

既存PDFも読み込んでSHA-256を再確認する場合:

```bash
make ingest INGEST_ARGS="--source-prefix zotero/ --verify-hash"
```

PDFはここに保存されます:

```text
rag_poc/papers/zotero/
```

PDF取得も差分更新です。既に同じPDFが保存されている場合は再ダウンロードしません。
通常の差分同期では、新規または変更された代表itemだけPDF添付を確認します。
全itemのPDF添付を確認し直したい場合だけ `--refresh-pdf-metadata` を付けます。

接続確認だけ:

```bash
uv run python rag_poc/zotero_sync.py --limit 5 --dry-run
```

## 次にやること

SQLite indexを必要に応じてQdrantやPostgres + pgvectorへ移行します。
