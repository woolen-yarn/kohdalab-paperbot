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
ollama pull qwen3:14b
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
PAPERBOT_TRANSLATION_MODEL=qwen3:14b
PAPERBOT_TRANSLATION_ENABLED=true
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
PAPER_WATCH_RSS_CROSSREF_FALLBACK_MAX_JOURNALS=6
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

`channel_not_found` になる場合は、`#paper` ではなくチャンネルIDを指定してください。
Paper Watchは研究室プロファイル語の `term_score` に加えて、候補abstractをembeddingし、
既存PDF RAG indexとの類似度 `rag_score` も使います。最終スコアは
`term_score + rag_score * PAPER_WATCH_RAG_WEIGHT` です。
Crossrefへのアクセスは控えめにしており、デフォルトでは1回の実行で最大2クエリ、
各10件だけ取得し、リクエスト間に1秒待ちます。`PAPER_WATCH_CONTACT_EMAIL` を
設定すると、Crossrefに `mailto` とUser-Agentで連絡先を伝えられます。
Crossrefが `429` や4XXを返した場合、その実行ではCrossref取得を止めます。
RSSはPR系、Nature系、AIP/APL系に分けてあり、1 feedあたり既定20件だけ取得し、
feed間に1秒待ちます。RSS URLが変わった場合は `.env` の `PAPER_WATCH_RSS_FEEDS`
で `id|group|journal|url` を `;` 区切りで上書きできます。
AIP/APLのRSSは出版社側で403になることがあるため、RSS groupが空だった場合だけ
ISSNで雑誌を絞った控えめなCrossref fallbackを使えます。fallback対象誌数は
`PAPER_WATCH_RSS_CROSSREF_FALLBACK_MAX_JOURNALS` で上限をかけます。
投稿文の英語解説は `PAPER_WATCH_SUMMARY_MODEL` が100-180 words程度の
研究者向けcommentaryとして作り、problem、material/system、method、physics、
novelty、significance、`Relevance to our research` を含めます。日本語化は
`PAPER_WATCH_TRANSLATION_MODEL` で構造を保ったまま行います。通常のSlack投稿は軽量表示で、
1論文ずつ個別メッセージに分け、タイトル、著者、雑誌/ソース、関連度ランク、
match、日英紹介、短いリンク、近い研究室PDFをコンパクトに表示します。
`PAPER_WATCH_VERBOSE_MESSAGE=true` でスコア詳細、`PAPER_WATCH_INCLUDE_ABSTRACT=true`
でAbstract表示を戻せます。
Zotero同期パイプラインは、PDF ingest後に `rag_poc/index/lab_profile.json` と
`rag_poc/index/lab_profile.md` も更新します。これは研究室の材料系、手法、物理
トピック、応用、著者、掲載誌をRAG indexから抽出したPaper Watch用の裏側
プロファイルです。

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

Synologyのタスクスケジューラでは、rootユーザーで分けて登録するのがおすすめです。
API/RSSアクセスを分散でき、Slack通知も読みやすくなります。

```bash
# 週1: arXiv
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources arxiv

# 月1 第1週: APS / Physical Review 系
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups pr,pr_ext

# 月1 第2週: Nature 系
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups nature,nature_ext

# 月1 第3週: AIP / Applied Physics Letters
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups aip

# 月1 第4週: nano / 2D materials 系
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups nano_2d

# 任意で月1または隔月: broad high-impact journals
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources rss --rss-groups broad_high

# 任意で月1: Crossref
cd /volume1/docker/paperbot && ./scripts/run_paper_watch.sh --sources crossref
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
