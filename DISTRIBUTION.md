# Distribution / 配布

Open **Actions → Distribute → Run workflow** on the default branch. A successful run builds and validates the selected commit, publishes a build-numbered release, and makes it the latest release. Select **verify_only** to build and test before publication.

既定ブランチの **Actions → Distribute → Run workflow** を1回実行します。ビルド・検証に成功すると配布番号を自動で付け、最新版として公開します。公開前のビルド確認には **verify_only** を選択します。

[Latest download / 最新版ZIP](https://github.com/woolen-yarn/kohdalab-paperbot/releases/latest/download/kohdalab-paperbot-source.zip) · [Previous distributions / 過去の配布](https://github.com/woolen-yarn/kohdalab-paperbot/releases) · [SHA-256](https://github.com/woolen-yarn/kohdalab-paperbot/releases/latest/download/SHA256SUMS.txt)

The fixed URL becomes available after the first Distribute publication. Python compile and unit tests, wheel and source package build. Live services need their configured environment.

固定URLはこのワークフローの初回公開後に利用できます。各版にはコミット・実行URL・SHA-256とソフトウェア検証の範囲を記録します。装置別の実機検証範囲は各アプリの記録を参照してください。

This job sends its distribution directly to Releases. Existing workflows may retain their own review artifacts. The application version stays in the package and the distribution number is recorded in BUILD-INFO.json.

配布ファイルは同じジョブからReleasesへ添付します。既存のテストや正式版作成ワークフローの成果物は、それぞれの保持期間に従います。アプリ内の版番号と配布番号の対応はBUILD-INFO.jsonで確認できます。
