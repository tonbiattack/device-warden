# 技術設計案

## コンポーネント
- WindowsトレイUI: ネイティブに近い軽量UI（技術選定はIssueで決定）
- ローカルBroker: ADB端末発見、端末登録、排他リース、永続化、イベント配信
- AI連携用CLI: `device-warden list|claim|release|heartbeat|status`
- OpenCode連携: セッションID・タイトル・プロジェクトをBrokerへ通知するプラグイン/フック
- その他AI: ツール名を指定してCLIから確保する。UIではツール名のみ表示

## API/内部モデル例
```text
Device {id, adb_serial?, alias?, model?, connection_state, ai_policy, last_seen}
Lease {id, device_id, owner_kind, tool?, session_id?, project?, acquired_at, heartbeat_at, state}
```

Leaseはデバイス単位でactiveを最大1件に制限。取得・解放はトランザクション処理。安定した端末IDと動的なadb_serialは分離する。

## 操作フロー
1. Brokerが `adb devices -l` 等からデバイス検出、以前登録した未接続端末とマージ
2. OpenCode等はBroker経由で空き端末をclaimしてlease_idを取得
3. AIは返された端末を対象に明示的なADBコマンドを実行
4. 完了時にrelease。異常終了・接続断は要確認へ遷移、誤って空きに戻さない
5. GUIは状態更新を監視し「自分が使ってよいか」を表示

## 将来
- adbラッパーによる管理外アクセス低減
- scrcpy起動
- 利用履歴、通知、複数PC
