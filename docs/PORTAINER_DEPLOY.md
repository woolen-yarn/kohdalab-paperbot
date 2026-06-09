# Portainer Deployment

PaperBotは、DS920+上にrepoを置いて運用できます。

おすすめは次の分担です。

```text
DS920+ local repo
  /volume1/docker/paperbot
  git pull
  docker compose build/up

Portainer
  containerの起動状態確認
  logs確認
  restart/stop/start
```

PortainerのGit repository Stackは、NAS上にrepoを置かない場合の別案です。

## 方針

```text
DS920+ host path
  /volume1/docker/paperbot
  /volume1/docker/paperbot/rag_poc/papers
  /volume1/docker/paperbot/rag_poc/index
  /volume1/docker/paperbot/logs

RTX PC
  Ollama: http://10.32.145.143:11434
```

`.env` はNAS上のrepoに置きますが、Gitにはcommitしません。

## 1. NASにrepoを置く

DS920+のSSHで:

```bash
cd /volume1/docker
git clone git@github.com-paperbot:Kohdalab/kohdalab-paperbot.git paperbot
cd /volume1/docker/paperbot

mkdir -p /volume1/docker/paperbot/rag_poc/papers
mkdir -p /volume1/docker/paperbot/rag_poc/index
mkdir -p /volume1/docker/paperbot/logs
```

既にclone済みなら:

```bash
cd /volume1/docker/paperbot
git pull
```

## 2. `.env` を作る

```bash
cd /volume1/docker/paperbot
cp .env.example .env
vi .env
```

最低限これを入れます。

```text
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
OLLAMA_BASE_URL=http://10.32.145.143:11434
OLLAMA_CHAT_MODEL=qwen3:8b
OLLAMA_EMBED_MODEL=nomic-embed-text
PAPERBOT_TOP_K=6
PAPERBOT_MAX_PER_SOURCE=3
PAPERBOT_LOG_LEVEL=INFO
```

## 3. PDF/indexを置く

MacからPDF/indexをコピーします。`NAS_IP` はDS920+のIPまたは名前に置き換えてください。

```bash
rsync -av /Users/kikuchikeito/projects/llm/rag_poc/papers/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/papers/

rsync -av /Users/kikuchikeito/projects/llm/rag_poc/index/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/index/
```

## 4. 起動

まずSSHから起動します。

```bash
cd /volume1/docker/paperbot
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
```

これでPortainer側にも `kohdalab-paperbot` containerが表示されます。

Portainerでは:

```text
Containers
-> kohdalab-paperbot
-> Logs / Restart / Stop / Start
```

## 5. 更新

コード更新:

```bash
cd /volume1/docker/paperbot
git pull
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
```

PDF/index更新:

```bash
cd /volume1/docker/paperbot
sudo docker compose -f docker-compose.nas.yml run --rm ingest
sudo docker compose -f docker-compose.nas.yml restart paperbot
```

## 6. Portainerだけでdeployしたい場合

PortainerだけでGitHubからdeployする場合は、Git repository Stackとしてdeployします。

```text
Stacks
-> Add stack
-> Name: paperbot
-> Build method: Repository
```

簡単に試す場合はpublic mirror:

```text
Repository URL:
  https://github.com/woolen-yarn/kohdalab-paperbot.git

Repository reference:
  refs/heads/master

Compose path:
  docker-compose.portainer.yml
```

Kohdalab private originを使う場合:

```text
Repository URL:
  git@github.com:Kohdalab/kohdalab-paperbot.git

Repository reference:
  refs/heads/master

Compose path:
  docker-compose.portainer.yml
```

private originの場合は、Portainer側にGit authentication/SSH keyを設定してください。

## 7. 確認

Portainer:

```text
Containers
-> kohdalab-paperbot
-> Logs
```

起動ログ:

```text
paperbot_start ollama_base_url=http://10.32.145.143:11434
```

Slack DMで:

```text
Persistent Spin Helixについて一文で教えて
```

## よくあるエラー

`variable is not set`:

Git repository Stackの場合は、PortainerのEnvironment variablesに値が入っていません。
NAS local repoの場合は、`/volume1/docker/paperbot/.env` がないか、中身が不足しています。

`path "/volume1/docker/paperbot" not found`:

PortainerのWeb editorでhost pathをbuild contextにしています。
Repository deployを使い、Compose pathを `docker-compose.portainer.yml` にしてください。
NAS local repoの場合は、SSHで `sudo docker compose -f docker-compose.nas.yml up -d --build paperbot` を使ってください。

`Index not found`:

`/volume1/docker/paperbot/rag_poc/index/chunks.jsonl` がありません。
