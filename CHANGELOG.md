# 変更履歴

## 0.2.0 — 2026-11-20

「作ったが運用されていない」状態の原因を調査し、使えなかった理由を潰したリリース。

### 追加

- `device-warden run --tool <name> -- <command>` — コマンド実行中は30秒ごとに自動でheartbeatを送り、成功時に解放する。ビルドやテストのような数分かかる作業を1リースで囲める。
- `device-warden-mcp` — MCPのstdioサーバ。`list` / `claim` / `release` / `adb` / `status` を提供し、リースの保持とheartbeatをこのプロセスが担当する。`lease_id` は外に出ない。追加依存なし。
- `device-warden-doctor` — Python、データフォルダ、Broker、`adb`、CLIのPATH、自動起動、**利用状況**を自己診断する。
- GUIに「最終利用」列。一度も確保されていない端末は `未使用` と表示する。
- `DEVICE_WARDEN_PORT` — Brokerの待受ポートを変更できる。テストが一時ポートを使うためのもの。
- mainへのpushとPull Requestでテストを回すCI（`.github/workflows/tests.yml`）。

### 修正

- `device-warden apps install <apk> --yes` が必ず「無効なリクエストです」で失敗していた。CLIが `apk`、Brokerが `apk_path` を期待していた。CLIのテストが `call` をモックしていたため、リクエストが実際にシリアライズされず見逃されていた。実Brokerを一時ポートで起動する結合テストを追加した。
- Windowsのコンソールで日本語が文字化けしていた。Pythonはcp932で書き、コンソールがUTF-8のときに壊れていた。コンソールのコードページに合わせるようにした。

### 変更

- バージョンの情報源を `device_warden/__init__.py` に一本化した。
- READMEのOpenCode連携に検証状況を明記し、OpenCode未導入の環境では動かないことを書いた。

### 既知の制約

- Codex・Command Code の実ツールでの検証は未実施（Issue #15）。
- 2つのOpenCodeセッションによる同時確保の確認は未実施。
- 別Windowsユーザーからの読み取り試験は未実施。
- Brokerを経由しない直接の `adb` は検出も阻止もできない。
