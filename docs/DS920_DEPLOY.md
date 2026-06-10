# DS920+ Deployment

DS920+をPaperBotの常時起動ノードにして、重いLLM/embeddingはRTX PCのOllamaへ投げます。

```text
Slack DM
  -> DS920+: paperbot container
  -> DS920+: rag_poc/index/chunks.sqlite3
  -> RTX PC: http://10.32.145.143:11434
  -> Slack reply
```

## 0. 前提

RTX PCでOllamaがLAN公開されていること:

```powershell
ollama pull gpt-oss:20b
ollama pull nomic-embed-text
```

DS920+から疎通確認:

```bash
curl -s http://10.32.145.143:11434/api/tags
```

## 1. DS920+にフォルダを作る

例:

```text
/volume1/docker/paperbot/
```

このリポジトリ一式をその中に置きます。

## 2. `.env` を作る

```bash
cd /volume1/docker/paperbot
cp .env.example .env
```

`.env` に実トークンを入れます。

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

OLLAMA_BASE_URL=http://10.32.145.143:11434
OLLAMA_CHAT_MODEL=gpt-oss:20b
OLLAMA_EMBED_MODEL=nomic-embed-text
PAPERBOT_TOP_K=6
PAPERBOT_SHORT_TOP_K=3
PAPERBOT_DEEP_TOP_K=8
SYNC_NOTIFY_CHANNEL=#paperbot-log
SYNC_NOTIFY_MODE=errors_only

ZOTERO_LIBRARY_TYPE=group
ZOTERO_LIBRARY_ID=1234567
ZOTERO_API_KEY=...
ZOTERO_PDF_WORKERS=1
```

## 3. PDF/indexを置く

Slack同期通知を使う場合は、PaperBotを `#paperbot-log` に招待してください。
`SYNC_NOTIFY_MODE=errors_only` なら失敗時だけ通知します。
`changes` にすると、失敗時に加えて新規/変更があった成功同期だけ通知します。
もしSlack側で `channel_not_found` になる場合は、`#paperbot-log` ではなくチャンネルIDを
`SYNC_NOTIFY_CHANNEL` に設定してください。

Paper Watchを使う場合は、PaperBotを `#paper` に招待し、`.env` に以下を入れます。

```text
PAPER_WATCH_CHANNEL=#paper
PAPER_WATCH_LOOKBACK_DAYS=14
PAPER_WATCH_MAX_RESULTS=80
PAPER_WATCH_POST_LIMIT=5
PAPER_WATCH_MIN_SCORE=6
PAPER_WATCH_BILINGUAL_INTRO=true
PAPER_WATCH_SUMMARY_MODEL=gpt-oss:20b
```

`channel_not_found` になる場合は、`#paper` ではなくチャンネルIDを指定してください。
Paper Watch v1はRAG類似度ではなく、研究室プロファイル語のterm scoreで候補を選びます。
投稿文の紹介はOllamaで日英併記生成します。

PDF:

```text
/volume1/docker/paperbot/rag_poc/papers/
```

既にMacで作ったindexを使う場合:

```text
/volume1/docker/paperbot/rag_poc/index/chunks.sqlite3
```

DS920+上で作り直す場合:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest
```

通常の `ingest` は差分更新です。既存PDFは通常、file size と mtime が同じならPDF本文を読まずに高速スキップされます。
完全な整合性確認をしたい場合だけ `--verify-hash` を付けると、既存PDFもSHA-256で検証します。
完全重複PDFは二重にembeddingされません。
chunk設定やembeddingモデルを変えて全PDFを作り直す場合だけ:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --rebuild
```

Zotero Group Libraryのメタデータだけ同期する場合:

```bash
docker compose -f docker-compose.nas.yml run --rm zotero
```

初回の通常同期では全top-level metadataを取得し、Zotero library versionをSQLiteの
`zotero_sync_state` に保存します。2回目以降はZotero APIの `since` parameterを使い、
前回version以降に変わったmetadataだけを取得します。
On later runs, only changed metadata is fetched from Zotero.

接続確認だけなら:

```bash
docker compose -f docker-compose.nas.yml run --rm zotero python rag_poc/zotero_sync.py --limit 5 --dry-run
```

Zoteroメタデータは同じSQLiteの `papers` テーブルに保存されます。
同一論文が複数Zotero itemとして登録されている場合は、DOIまたはtitle+yearでduplicate判定されます。
重複を除いた一覧はSQLite viewの `unique_papers` を使います。

代表itemだけ添付PDFも取得する場合:

```bash
docker compose -f docker-compose.nas.yml run --rm zotero python rag_poc/zotero_sync.py --download-pdfs
docker compose -f docker-compose.nas.yml run --rm ingest
```

PDFは `rag_poc/papers/zotero/` に保存されます。既に同じPDFがある場合は差分判定でスキップされます。
通常ログでは unchanged PDF は1件ずつ表示しません。全部見たい場合だけ `--verbose-pdfs` を付けます。
通常実行では取得済みPDFとPDFなし確認済みitemはZoteroのchild attachment確認もスキップします。
通常の差分同期では、新規または変更された代表itemだけPDF添付を確認します。
The normal path does not check child attachments for every paper.
PDF取得の並列数はAPIを叩きすぎないよう既定で `1` です。大量の初回取り込み時だけ
`.env` の `ZOTERO_PDF_WORKERS` または `--pdf-workers` で増やしてください。
全itemのPDF添付を再確認したい場合だけ `--refresh-pdf-metadata` を付けます。

Zotero由来PDFだけでRAG indexを作る場合、初回だけ:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --rebuild --source-prefix zotero/
```

以後の差分更新:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --source-prefix zotero/
```

既存PDFも読み込んでSHA-256を再確認する場合:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest python rag_poc/ingest.py --source-prefix zotero/ --verify-hash
```

まとめて実行する場合:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/sync_zotero_pipeline.sh
```

全metadataを明示的に取り直す場合:

```bash
cd /volume1/docker/paperbot
sudo ZOTERO_ARGS="--all --download-pdfs" ./scripts/sync_zotero_pipeline.sh
```

全itemのPDF添付を明示的に再確認する場合:

```bash
cd /volume1/docker/paperbot
sudo ZOTERO_ARGS="--download-pdfs --refresh-pdf-metadata" ./scripts/sync_zotero_pipeline.sh
```

初回だけZotero由来PDFでRAG indexを作り直す場合:

```bash
cd /volume1/docker/paperbot
sudo REBUILD=1 ./scripts/sync_zotero_pipeline.sh
```

Synologyのタスクスケジューラに登録する場合も、このコマンドを使います。

## 3.5 Paper Watch

新着論文を `#paper` に紹介する場合:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/run_paper_watch.sh
```

投稿せずに候補だけ確認する場合:

```bash
cd /volume1/docker/paperbot
sudo ./scripts/run_paper_watch.sh --dry-run
```

Synologyのタスクスケジューラでは、rootユーザーで例えば週1回:

```bash
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh
```

ログ:

```text
/volume1/docker/paperbot/logs/paper_watch.log
```

## 4. 起動

SSHで実行できる場合:

```bash
cd /volume1/docker/paperbot
docker compose -f docker-compose.nas.yml up -d --build paperbot
```

ログ確認:

```bash
docker compose -f docker-compose.nas.yml logs -f paperbot
```

Synology Container Managerを使う場合:

1. `Container Manager` を開く
2. `Project` を作成
3. Pathに `/volume1/docker/paperbot` を指定
4. Compose fileに `docker-compose.nas.yml` を指定
5. Buildして起動

## 5. 更新

PDFを追加したら:

```bash
cd /volume1/docker/paperbot
docker compose -f docker-compose.nas.yml run --rm ingest
docker compose -f docker-compose.nas.yml restart paperbot
```

コードを更新したら:

```bash
cd /volume1/docker/paperbot
docker compose -f docker-compose.nas.yml up -d --build paperbot
```

## 6. ログ

コンテナログ:

```bash
docker compose -f docker-compose.nas.yml logs -f paperbot
```

アプリログ:

```text
/volume1/docker/paperbot/logs/paperbot.log
```

## 7. トラブルシュート

Ollamaへつながらない:

```bash
curl -s http://10.32.145.143:11434/api/tags
```

Slackへつながらない:

- `.env` の `SLACK_BOT_TOKEN` が `xoxb-...` か確認
- `.env` の `SLACK_APP_TOKEN` が `xapp-...` か確認
- Slack AppのSocket ModeがONか確認

運用状態を確認したい:

Slack DMで:

```text
status
```

PDFを更新したのに回答が変わらない:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest
docker compose -f docker-compose.nas.yml restart paperbot
```
