# DS920+ Deployment

DS920+をPaperBotの常時起動ノードにして、重いLLM/embeddingはRTX PCのOllamaへ投げます。

```text
Slack DM
  -> DS920+: paperbot container
  -> DS920+: rag_poc/index/chunks.jsonl
  -> RTX PC: http://10.32.145.143:11434
  -> Slack reply
```

## 0. 前提

RTX PCでOllamaがLAN公開されていること:

```powershell
ollama pull qwen3:8b
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
OLLAMA_CHAT_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=nomic-embed-text
```

## 3. PDF/indexを置く

PDF:

```text
/volume1/docker/paperbot/rag_poc/papers/
```

既にMacで作ったindexを使う場合:

```text
/volume1/docker/paperbot/rag_poc/index/chunks.jsonl
```

DS920+上で作り直す場合:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest
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

PDFを更新したのに回答が変わらない:

```bash
docker compose -f docker-compose.nas.yml run --rm ingest
docker compose -f docker-compose.nas.yml restart paperbot
```
