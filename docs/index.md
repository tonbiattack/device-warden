# Device Warden

**触る前に、その Android 端末を自分が使ってよいか分かる。**

Device Warden は、Windows のタスクトレイに常駐する端末管理ツールです。手元の Android 端末・エミュレーターを、人間と複数の AI コーディングエージェントの間で取り合いにならないよう調整します。

- [プライバシーポリシー](PRIVACY.html)
- [GitHub リポジトリ](https://github.com/tonbiattack/device-warden)
- [Microsoft Store](https://apps.microsoft.com/detail/9P1HSTHZX5KH)

## 主な機能

- 端末ごとに同時1件のリースを SQLite に記録し、`claim` / `heartbeat` / `release` で排他する
- 接続状態（USB / Wi-Fi / 未接続 / 未承認）を追跡し、切断時は「要確認」へ移す
- 人間と AI の利用を区別し、AI ツール名・セッション・プロジェクトを表示する
- localhost のトークン認証 API と、OpenCode プラグイン / MCP サーバ / CLI を提供する
- 通信はローカル完結。利用状況の外部送信、広告、アカウント登録はない
