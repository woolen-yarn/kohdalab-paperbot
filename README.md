# KohdaLab PaperBot

[![CI](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml/badge.svg)](https://github.com/woolen-yarn/kohdalab-paperbot/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Release](https://img.shields.io/github/v/release/woolen-yarn/kohdalab-paperbot?sort=semver)](https://github.com/woolen-yarn/kohdalab-paperbot/releases)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)](pyproject.toml)

KohdaLab PaperBotは、研究室の論文を収集・検索・推薦するシステムです。Zoteroを論文情報の正本とし、Synology NASにPDFとSQLiteデータベースを保存します。埋め込みと文章生成はRTX PC上のllama.cppで実行し、利用者とのやり取りにはSlackを使います。

## できること

- Zotero Group Libraryの論文情報とPDFをNASへ差分同期する
- PDFをRAGインデックスへ登録し、SlackのDMから根拠付きで質問する
- arXivや主要ジャーナルの新着論文を収集し、研究室との関連度を0〜100点で評価する
- 高得点の論文を週次・月次でSlackの`#paper`へ投稿する
- 同期や処理の結果を`#paperbot-log`へ通知する

## システム構成

| 場所 | 役割 |
| --- | --- |
| Synology DS920+ | Docker、定期実行、PDF、SQLite、Slack Bot |
| RTX PC | llama.cppによる埋め込み、回答生成、日本語化 |
| Zotero | 論文情報と添付PDFの正本 |
| Slack | RAG質問、論文推薦、運用通知 |

詳細な構成図は[システム構成](docs/ARCHITECTURE.md)を参照してください。

## 導入

本番環境は次のディレクトリを前提とします。

```text
/volume1/docker/paperbot
```

初回構築、`.env`、llama.cpp、Slack、Zotero、DSMタスクスケジューラの設定は[DS920+導入・運用手順](docs/DS920_DEPLOY.md)にまとめています。

基本的な起動と初回同期は次のとおりです。

```bash
cd /volume1/docker/paperbot
cp .env.example .env
sudo docker compose -f docker-compose.nas.yml up -d --build paperbot
sudo ./scripts/sync_zotero_pipeline.sh
```

## Slackでの利用

- BotへのDM：登録済み論文をRAG検索して回答する
- `status`または`状態`：llama.cpp、SQLite、Zotero、インデックスの状態を表示する
- `sources`：直前の回答に使った出典を表示する
- `#paper`：Paper Watchが推薦論文を投稿する
- `#paperbot-log`：同期や定期処理の成否を通知する

## 論文推薦スコア

Paper Watchは、候補論文を0〜100点で評価します。

\[
S = 45K + 35R + 10C + 5A + 5J
\]

| 記号 | 配点 | 内容 |
| --- | ---: | --- |
| `K` | 45 | タイトル・要旨に含まれる重要語 |
| `R` | 35 | 研究室PDFとのRAG類似度 |
| `C` | 10 | 材料・手法・物理・応用のテーマ組み合わせ |
| `A` | 5 | 研究室プロファイルまたは設定で優先された著者 |
| `J` | 5 | 研究室プロファイルまたは設定で優先された雑誌 |

重要語はタイトル一致を要旨一致の1.5倍として扱います。同義語は同じ概念としてまとめ、最も強い一致だけを採用します。複数の概念が一致した場合は、後続の加点を既定で0.85倍ずつ弱めます。

RAGはタイトルと要旨を別々に埋め込み、各研究室PDFから最も近いチャンクを1件ずつ選びます。そのうち上位3論文を`0.6 / 0.3 / 0.1`で集約し、固定基準と候補集合内の相対基準を組み合わせて補正します。

RAG取り込みが成功するたびに、`lab_profile.json`と`lab_profile.md`が更新されます。ここから重要語、テーマ、著者、雑誌、除外寄りの語を取得するため、研究室の論文集合の変化が次回以降のスコアへ反映されます。

計算式、Termスコア、RAG補正、各環境変数、DB保存値の詳細は[スコアリング仕様](docs/SCORING.md)を参照してください。

## 既存データの再スコアリング

現在のプロファイルと計算方式を既存の未投稿論文へ適用できます。まず書き込みなしで確認します。

```bash
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180 --dry-run
sudo ./scripts/run_paper_watch.sh --mode rescore --lookback-days 180
```

書き込み実行前に、`paper_watch.sqlite3.backup-日時`というSQLiteバックアップを自動作成します。DBスキーマと投稿済み履歴は変更しません。投稿済み論文も対象にする場合だけ、明示的に`--include-posted`を追加します。

## 主な保存先

| NAS上のパス | 内容 |
| --- | --- |
| `rag_poc/papers/zotero/` | Zoteroから同期したPDF |
| `rag_poc/index/chunks.sqlite3` | 論文、チャンク、埋め込み |
| `rag_poc/index/paper_watch.sqlite3` | 新着論文、分類、スコア、投稿状態 |
| `rag_poc/index/lab_profile.json` | スコア計算用の研究室プロファイル |
| `rag_poc/index/lab_profile.md` | 人が確認するための研究室プロファイル |
| `logs/` | 同期とPaper Watchのログ |

`.env`、PDF、SQLite、埋め込み、ログはGitへ登録しません。

## ドキュメント

- [DS920+導入・運用手順](docs/DS920_DEPLOY.md)：本番構築、定期実行、更新、障害対応
- [スコアリング仕様](docs/SCORING.md)：Term、RAG、最終スコア、再計算
- [システム構成](docs/ARCHITECTURE.md)：NAS、RTX PC、Slack、Zoteroの関係
- [ローカルモデル評価](docs/MODEL_EVALUATION.md)：PaperBot実課題の比較結果と採用構成
- [RAG内部仕様](rag_poc/README.md)：同期、取り込み、検索、Paper Watch
- [Git運用](docs/GIT_WORKFLOW.md)：`origin`と`personal`、PR手順
- [変更履歴](CHANGELOG.md)：リリースごとの変更内容

## 開発

ローカル確認とCIは同じ検査を実行します。

```bash
make check
```

## リリース

現在の安定版は[v1.0.0](https://github.com/woolen-yarn/kohdalab-paperbot/releases/tag/v1.0.0)です。

## ライセンス

[MIT License](LICENSE)
