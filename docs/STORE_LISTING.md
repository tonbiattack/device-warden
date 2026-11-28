# Microsoft Store 掲載素材（Issue #45）

Device Warden（Store ID `9P1HSTHZX5KH`）の Store listings（日本語）用の下書き。Partner Center への入力は Muse 側。

## アプリ名

Device Warden

## 短い説明（1文）

Android 実機とエミュレーターを、人間と複数の AI コーディングエージェントの間で取り合いにならないよう管理する Windows トレイツールです。

## 説明文

**触る前に、その Android 端末を自分が使ってよいか分かる。**

Device Warden は、Windows のタスクトレイに常駐する端末管理ツールです。手元の Android 端末・エミュレーターを、人間と複数の AI コーディングエージェントの間で共有するときに起きます。

- 2つのエージェントが同じ端末を選び、片方のテスト結果を静かに壊す
- 抜けている端末が空いているとは限らず、ADB でつながっている端末も暇とは限らない

Device Warden は端末ごとに同時1件のリースをローカルの SQLite に記録し、その状態をトレイの画面と、認証付きの小さな API で公開します。エージェントは端末を使う前に `claim` し、作業中は `heartbeat` を送り、終わったら `release` します。確認してから取得するのではなく、DB の制約で排他するため、同時に2つのセッションが同じ端末を保持することはありません。

### 主な機能

- **リース管理**: `claim` / `heartbeat` / `release`。切断や Broker の再起動でリースを勝手に解放せず、「要確認」へ移します
- **接続状態の追跡**: USB / Wi-Fi / 未接続 / 未承認を表示
- **人間と AI の区別**: AI ツール名・セッション・プロジェクト・作業ディレクトリを一覧に表示
- **トレイ常駐**: 状態で色が変わるアイコンと通知。二重起動時は既存ウィンドウを前面に表示
- **外部連携**: localhost のトークン認証 API、OpenCode プラグイン、MCP サーバ、CLI
- **scrcpy 連携**: 確保した端末の画面を開く（scrcpy は別途必要）

### 動作環境

- Windows 10 / 11（x64）
- Android SDK Platform-Tools（`adb`）が別途必要です。`adb` を PATH に通し、端末の USB デバッグを有効にしてください
- scrcpy は任意です。画面ミラーリングを使う場合のみ必要です
- 通信は localhost（127.0.0.1）のトークン認証 API のみで、利用状況や端末情報を外部のサーバーへ送信しません

### こんな人向けです

複数の AI コーディングエージェントと実機を共有する開発者向けの、対象を絞ったツールです。1人で1台を使うだけなら、通常の `adb` だけで足ります。

プライバシーポリシー: https://tonbiattack.github.io/device-warden/PRIVACY.html

## 製品の特徴（最大10件、各200文字以内）

1. 端末ごとに同時1件のリースを SQLite に記録し、claim / heartbeat / release で排他します。確認してから取得するのではなく、DB の制約で取り合いを防ぎます。
2. 接続状態（USB / Wi-Fi / 未接続 / 未承認）を追跡します。切断や Broker の再起動でリースを勝手に解放せず、「要確認」へ移します。
3. 人間と AI の利用を区別し、AI ツール名・セッション・プロジェクト・作業ディレクトリを一覧に表示します。
4. 状態で色が変わるトレイアイコンと通知で、確保・解放をデスクトップから確認できます。
5. localhost のトークン認証 API を提供し、OpenCode プラグイン・MCP サーバ・CLI から同じ状態を参照できます。
6. 確保した端末の画面を scrcpy で開けます（scrcpy は別途必要）。
7. 監査ログで、確保・解放・強制解放の履歴を確認できます。
8. 利用状況の外部送信、広告、アカウント登録はありません。データは PC 内の SQLite にのみ保存します。

## スクリーンショット

Store の要件（1366x768 以上、PNG）を満たす 1400x800 の PNG です。実在の端末シリアルを含めないよう、ダミーデータで GUI を描画して撮影しています（`scripts/capture-store-screenshots.py`）。

| 順序 | ファイル | 内容 |
| --- | --- | --- |
| 1 | `docs/store/screenshots/01-devices.png` | 端末一覧。未確保・AI使用中・自分が使用中・接続切れが並ぶ画面 |
| 2 | `docs/store/screenshots/02-apps.png` | アプリタブ。端末ごとのインストール済みバージョンを比較する画面 |

撮り直す場合:

```powershell
python scripts/capture-store-screenshots.py
```

## 審査ノート（Notes for certification）

- **runFullTrust が必要な理由**: 端末操作のため `adb` / `scrcpy` を外部プロセスとして起動し、`%USERPROFILE%\.device-warden` に SQLite DB とトークンを読み書きし、タスクトレイに常駐するためです。管理者権限は要求しません。
- **adb が別途必要**: 本アプリは Android SDK Platform-Tools を同梱しません。`adb` が無い環境でもアプリは起動し、端末一覧は空のまま、自己診断（doctor）が `adb` の不足を警告します。
- **端末が未接続でも起動**: USB デバッグの端末を接続していなくても、トレイと一覧画面は表示されます。
- **localhost API**: `127.0.0.1:47631` で待ち受け、`%USERPROFILE%\.device-warden\broker.token` のトークン認証を必須にしています。外部インターフェースでは待ち受けません。
- **外部通信**: 利用状況の送信はありません。カタログ機能（Obtainium 連携）を使ったときだけ、GitHub の公開リリース情報を取得するために `gh` を呼びます。
- **確認手順の例**: アプリを起動 → トレイアイコンと一覧画面を確認 → 端末を接続すると一覧に現れる → 端末を選んで「自分が確保」→「解放」。AI からの利用は CLI / OpenCode プラグイン / MCP から同じ API を使います。

## 提出前チェック

- [x] 説明文に `adb` が別途必要であることを明記
- [x] スクリーンショットを 2 件用意（1366x768 以上）
- [x] プライバシーポリシー URL（https://tonbiattack.github.io/device-warden/PRIVACY.html）
- [ ] Partner Center への入力（Muse 側）
- [ ] 年齢レーティング、価格（Free）の確認（Muse 側）
