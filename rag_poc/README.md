# KohdaLab PaperBot RAG PoC

Mac上でPDF 10本を対象にした最小RAGを動かすためのPoCです。

構成:

```text
Mac
├─ rag_poc/papers/       PDFを置く
├─ ingest.py             PDF抽出 + chunk化 + embedding
├─ ask.py                検索 + Ollama回答
└─ index/chunks.sqlite3  作成されるRAG index

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

作成されるファイル:

```text
rag_poc/index/chunks.sqlite3
```

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

## 次にやること

SQLite indexを必要に応じてQdrantやPostgres + pgvectorへ移行します。
