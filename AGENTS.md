# Device Warden 作業ルール

このファイルはこのリポジトリで作業するAIエージェント向けの指示です。

## 回答言語

- **すべての回答・報告・要約は日本語で書く。** コード内のコメントや識別子、コミットメッセージの慣例はこの限りではないが、ユーザー向けの説明は必ず日本語にする。
- README.md も日本語で書く。コード内のdocstringやコメントは英語のままでよい。

## Android端末を使うときの必須手順

Device Warden の Broker を経由せずに `adb` を直接実行すると、他のAIセッションや人間の作業と衝突する。端末を操作する前に必ず以下を守る。

1. `device_warden` ツール（または `device-warden` CLI）で `list` を実行し、対象端末の `id` を確認する。
2. `claim` で端末を確保する。`lease_id` を受け取り、作業が終わるまで保持する。
   - エージェントは OpenCode の `device_warden` ツールを使う。これによりセッションIDが自動で記録され、heartbeatも自動送信される。
   - CLIを使う場合も `--tool` に自分のツール名を必ず指定する。
3. 端末操作は `adb` サブコマンドではなく `device_warden` の `adb` アクション（または `device-warden adb <lease_id> ...`）で行う。対象端末は `-s` で自動指定され、取り違えを防げる。
4. 作業が終わったら必ず `release` する。セッション終了時の自動解放はない。
5. `claim` が失敗した場合は、別の利用者が確保中か、AI利用禁止か、未接続である。**直接 `adb` に切り替えず**、ユーザーに状況を伝える。

### 禁止事項

- 確保せずに端末を操作すること。
- `claim` に失敗した際のフォールバックとして `adb` を直接実行すること。
- 他者の `lease_id` を使った解放、または確認なしの強制解放。
- 接続断や heartbeat 停止を理由に、リースを勝手に空きとして扱うこと。

### 制約の理解

- 管理外の直接 `adb` 操作は仕様上検出・阻止できない。この制約を「対策済み」と説明しない。
- 検出のためのヒューリスティック（プロセス監視やポート監視）は短命なコマンドを取り逃すため、実装しない方針とする。運用ルールとUI/READMEの注意書きで担保する。
- OPENCODEのプラグインは `integrations/opencode` にあり、グローバル設定 `~/.config/opencode/opencode.json` の `plugins` に登録済み。

## 破壊的コマンドの安全規則

2026-11-27、検証スクリプトが一時フォルダの変数に `$home` を使ったことで、`Remove-Item $home -Recurse` が `C:\Users\<user>` 全体を対象に実行され、プロファイルの一部を削除した。`$HOME` / `$PROFILE` は読み取り専用で、代入は失敗するが変数は元の値のまま残る。

以下を守る。

- 一時フォルダの変数に `$home` / `$profile` / `$env:USERPROFILE` を使わない。`$tempHome` のように用途が分かる名前にする。
- `Remove-Item -Recurse` を直接書かない。削除は `scripts/Safety.psm1` の `Remove-SafeTempDir` を使う。TEMP の外、TEMP 自体、ワイルドカード、再解析ポイント（ジャンクション）は拒否される。
- 一時フォルダは `New-SafeTempDir -Purpose <用途>` で作り、同じパスだけを削除する。
- 自分で直接削除する必要がある場合は、実行前に解決後の絶対パスを表示し、`%TEMP%` 配下であることを確認する。`-Recurse` と変数の組み合わせは特に確認する。
- 安全補助の変更は `scripts/Test-Safety.ps1` で検証する（CI でも実行される）。

## 開発時の注意

- 状態モデル（接続状態・リース・AIポリシー）は独立した軸として扱う。[docs/SPEC.md](docs/SPEC.md) に従う。
- 接続断・Broker再起動・heartbeat停止でリースを自動解放しない。
- テストは `python -m unittest discover -s tests -v`。プラグインは `node integrations/opencode/test.mjs`。
- 端末シリアル、IP、ローカルパス、セッション名、Brokerトークンをコミットしない。データは `%USERPROFILE%\.device-warden` にあり、gitignore済み。
