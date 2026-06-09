# Git Workflow

このrepoはGitHub private repoで管理し、DS920+はGitから取得してDocker Composeで起動します。

```text
Mac
  edit -> commit -> push

GitHub private repo
  source of code/config templates

DS920+
  git pull -> docker compose up -d --build paperbot

RTX PC
  Ollama API server
```

## Mac側

```bash
cd /Users/kikuchikeito/projects/llm
git status
git add .
git commit -m "Update PaperBot"
git push
```

GitHubに入れるもの:

```text
Dockerfile
docker-compose.nas.yml
bot.py
rag_poc/*.py
requirements.txt
README.md
docs/
.env.example
```

GitHubに入れないもの:

```text
.env
logs/
rag_poc/papers/*.pdf
rag_poc/index/
.venv/
__pycache__/
```

## DS920+ 初回

```bash
cd /volume1/docker
git clone https://github.com/woolen-yarn/kohdalab-paperbot.git paperbot
cd paperbot
cp .env.example .env
```

`.env` にSlack tokenを入れます。

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
OLLAMA_BASE_URL=http://10.32.145.143:11434
```

PDFとindexを配置します。

```text
/volume1/docker/paperbot/rag_poc/papers/
/volume1/docker/paperbot/rag_poc/index/chunks.jsonl
```

起動します。

```bash
docker compose -f docker-compose.nas.yml up -d --build paperbot
```

## DS920+ 更新

```bash
cd /volume1/docker/paperbot
sh scripts/deploy_nas.sh
```

PDFを更新したとき:

```bash
cd /volume1/docker/paperbot
sh scripts/reindex_nas.sh
```

## Portainer

Portainerを使う場合は、StackのGit repository機能よりも、最初はDS920+上で `git pull` してからPortainerでredeployする方式が簡単です。

Stack settings:

```text
Name: paperbot
Path: /volume1/docker/paperbot
Compose file: docker-compose.nas.yml
```

更新時:

```text
DS920+ SSH: sh scripts/deploy_nas.sh
Portainer: Stackの状態とログを確認
```
