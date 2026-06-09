# Portainer Deployment

PortainerだけでPaperBotを起動する場合は、Git repository Stackとしてdeployします。

## 方針

```text
Portainer
  Git repositoryからbuild contextを取得
  Environment variablesでSlack tokenを渡す

DS920+ host path
  /volume1/docker/paperbot/rag_poc/papers
  /volume1/docker/paperbot/rag_poc/index
  /volume1/docker/paperbot/logs

RTX PC
  Ollama: http://10.32.145.143:11434
```

`stack.env` や `.env` をGit repoに入れません。

## 1. データ用フォルダを作る

DS920+のSSHで:

```bash
mkdir -p /volume1/docker/paperbot/rag_poc/papers
mkdir -p /volume1/docker/paperbot/rag_poc/index
mkdir -p /volume1/docker/paperbot/logs
```

MacからPDF/indexをコピーします。

```bash
rsync -av /Users/kikuchikeito/projects/llm/rag_poc/papers/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/papers/

rsync -av /Users/kikuchikeito/projects/llm/rag_poc/index/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/index/
```

## 2. Portainer Stackを作る

Portainer:

```text
Stacks
-> Add stack
-> Name: paperbot
-> Build method: Repository
```

Repositoryは2通りあります。

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

## 3. Environment variables

PortainerのStack画面下部のEnvironment variablesに追加します。

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

その後、`Deploy the stack` を押します。

## 4. 確認

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

## 5. 更新

GitHubにpush後、Portainer Stackの画面で:

```text
Pull and redeploy
```

PDF/indexを更新した場合は、データフォルダ側を更新してからcontainerをrestartします。

## よくあるエラー

`variable is not set`:

PortainerのEnvironment variablesに値が入っていません。

`path "/volume1/docker/paperbot" not found`:

Web editorでhost pathをbuild contextにしています。
Repository deployを使い、Compose pathを `docker-compose.portainer.yml` にしてください。

`Index not found`:

`/volume1/docker/paperbot/rag_poc/index/chunks.jsonl` がありません。
