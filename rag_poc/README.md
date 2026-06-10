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
