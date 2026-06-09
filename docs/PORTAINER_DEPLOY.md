# Portainer Stack Deployment

DS920+上にrepoを置き、そのrepoをbuild contextにして、最初からPortainer StackとしてPaperBotを作ります。

```text
Portainer Stack
  compose: docker-compose.stack-local.yml
  build context: /volume1/docker/paperbot
  env file: /volume1/docker/paperbot/.env
  service: paperbot only

DS920+ local repo
  /volume1/docker/paperbot

RTX PC
  Ollama: http://10.32.145.143:11434
```

この方式では、Slack tokenをPortainerのEnvironment variablesに入れません。
NAS上の `/volume1/docker/paperbot/.env` だけを使います。

## 1. NASにrepoを置く

DS920+へSSHで入ります。

```bash
ssh Kohdalab@NAS_IP
```

repoがまだ無い場合:

```bash
cd /volume1/docker
git clone git@github.com-paperbot:Kohdalab/kohdalab-paperbot.git paperbot
cd /volume1/docker/paperbot
```

repoが既にある場合:

```bash
cd /volume1/docker/paperbot
git pull
```

必要フォルダを作ります。

```bash
mkdir -p rag_poc/papers
mkdir -p rag_poc/index
mkdir -p logs
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

`.env` はGitにはcommitしません。

## 3. PDF/indexを置く

最初は空のままでも構いません。PDFをGitHubへcommitしないため、PDFとindexはNASまたはMacのローカルにだけ置きます。

必要になったら、Mac側からNASへコピーします。`NAS_IP` はDS920+のIPまたはホスト名に置き換えてください。

```bash
rsync -av /Users/kikuchikeito/projects/llm/rag_poc/papers/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/papers/

rsync -av /Users/kikuchikeito/projects/llm/rag_poc/index/ \
  Kohdalab@NAS_IP:/volume1/docker/paperbot/rag_poc/index/
```

## 4. Portainerがrepoを見えるか確認

Portainer StackでNAS上のrepoをbuild contextにするには、Portainer containerから `/volume1/docker/paperbot` が見えている必要があります。

NASのSSHでPortainer container名を確認します。

```bash
sudo docker ps --format 'table {{.Names}}\t{{.Image}}' | grep -i portainer
```

例えばcontainer名が `portainer` なら、mountを確認します。

```bash
sudo docker inspect portainer --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'
```

ここで次があればOKです。

```text
/volume1/docker -> /volume1/docker
```

見えない場合、Portainer containerに次のvolume mountを追加してください。

```text
Host path:
  /volume1/docker

Container path:
  /volume1/docker

Mode:
  read-only でも可
```

Synology Container ManagerからPortainer containerを編集する場合は、Portainerを一度停止し、volumeに上記を追加して起動し直します。

## 5. PortainerでStackを作る

Portainer:

```text
Stacks
-> Add stack
-> Name: paperbot
-> Build method: Web editor
```

Web editorに、`docker-compose.stack-local.yml` の中身を貼ります。

NASのSSHで表示する場合:

```bash
cat /volume1/docker/paperbot/docker-compose.stack-local.yml
```

貼り付けたら:

```text
Deploy the stack
```

このcomposeは以下を使います。

```text
service:
  paperbot

build context:
  /volume1/docker/paperbot

env file:
  /volume1/docker/paperbot/.env

volumes:
  /volume1/docker/paperbot/rag_poc/papers
  /volume1/docker/paperbot/rag_poc/index
  /volume1/docker/paperbot/logs
```

## 6. 確認

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

## 7. 更新

コードを更新する場合:

```bash
cd /volume1/docker/paperbot
git pull
```

その後、Portainer:

```text
Stacks
-> paperbot
-> Editor
-> Update the stack
```

PDF/indexを更新する場合は、PDFを追加してから `ingest` を実行します。

`docker-compose.stack-local.yml` は本番Stack用なので、常時起動する `paperbot` serviceだけを入れています。
index作成は、必要になってからMac側で実行してNASへコピーするか、NASのSSHで `docker-compose.nas.yml` を使います。

```bash
cd /volume1/docker/paperbot
sudo docker compose -f docker-compose.nas.yml run --rm ingest
```

その後、Portainerで `kohdalab-paperbot` をrestartします。

## よくあるエラー

`env file /volume1/docker/paperbot/.env not found`:

Portainer containerから `/volume1/docker/paperbot/.env` が見えていません。
Portainer containerに `/volume1/docker:/volume1/docker` をmountしてください。

`path "/volume1/docker/paperbot" not found`:

Portainer containerからbuild contextが見えていません。
Portainer containerに `/volume1/docker:/volume1/docker` をmountしてください。

`variable is not set`:

`docker-compose.stack-local.yml` では基本的に出ないはずです。
もし出る場合は、別のcompose fileをStackに貼っている可能性があります。

`Index not found`:

`/volume1/docker/paperbot/rag_poc/index/chunks.jsonl` がありません。
Macで作ったindexをコピーするか、`ingest` を実行してください。
