# Device Warden

**触る前に、そのAndroid端末を自分が使ってよいか分かる。**

Device Warden は、Windows のタスクトレイに常駐する軽量な端末管理ツールです。手元のAndroid端末・エミュレーターを、人間と複数のAIコーディングエージェントの間で取り合いにならないよう調整します。誰がどの端末を確保しているか、接続されているか、AIが使ってよいかを一覧で判断できます。

<p align="center">
  <img src="assets/app-icon.svg" width="96" alt="Device Warden のアイコン">
</p>

<p align="center">
  <img src="docs/screenshot.png" alt="Device Warden の端末一覧画面" width="900">
</p>

```text
端末                ADB                利用可否              確保者
Redmi Pad SE        connected / usb    管理内では未確保      —
Pixel 8             connected / wifi   AI使用中              OpenCode / ses_example…
Galaxy S24          connected / usb    自分が使用中          自分
emulator-5554       disconnected       接続切れ・要確認      Codex
OPPO CPH2699        disconnected       管理内では未確保（ADB未接続）  —
```

## なぜ必要か

Windows に複数の実機とエミュレーターがつながっていると、AIエージェントと人間が同じ端末を取り合って、次の問題が起きます。

- **衝突** — 2つのエージェントが同じ端末を選び、片方のテスト結果を静かに壊す。
- **誤解** — 抜けている端末が空いているとは限らず、ADBでつながっている端末も暇とは限らない。

Device Warden は端末ごとに同時1件のリースをローカルのSQLiteに記録し、その状態をトレイUIと、認証付きの小さなCLI/HTTP APIで公開します。

## 主な機能

- **リースBroker** — `claim` / `heartbeat` / `release` / `list` を提供。端末あたり有効なリースは1件だけで、確認してから取得するのではなくDB制約で排他する。
- **接続状態の追跡** — `connected` / `disconnected` / `unauthorized` / `unknown`。切断や再起動でリースを勝手に解放せず、**要確認**へ移す。
- **独立した3つの軸** — ADB接続、リースの所有者、AI利用ポリシー（許可/禁止）は互いを上書きしない。
- **人間とAIの区別** — OpenCode はセッションIDに加えてタイトル・プロジェクト・作業ディレクトリを表示。Codex や Command Code などはツール名で表示し、同じツールの複数ワーカーも個別のリースで区別する。
- **OpenCodeプラグイン** — 確保時に実際のセッションIDを渡し、30秒ごとに自動でheartbeatを送る。解放は明示操作のみで、セッション終了では解放しない。
- **ADBの仲介** — リース経由の `adb` は対象を `-s <serial>` で固定し、許可するサブコマンドを限定する。
- **監査ログ** — 確保・解放・強制解放の所有者を記録。`device-warden audit` またはGUIの「履歴」で確認できる。
- **トレイ中心の操作** — 状態で色が変わる盾アイコン、Windowsサインイン時の自動起動、二重起動時は既存ウィンドウを前面に表示。

## 動作環境

| 要件 | 補足 |
| --- | --- |
| Windows 11（Windows 10 でも動作する見込み） | トレイと自動起動はWindows APIを使用 |
| Python 3.11 以上 | `py -m venv` を推奨 |
| Android SDK Platform Tools（`adb`）が `PATH` 上 | `adb devices -l` が通ること |
| Node.js 24 以上 | OpenCodeプラグインのテスト用 |

## インストール

```powershell
git clone https://github.com/tonbiattack/device-warden.git
cd device-warden
py -m venv .venv
.\.venv\Scripts\python -m pip install -e .
```

Brokerを起動し、別のターミナルでトレイを起動します。

```powershell
.\.venv\Scripts\device-warden-broker
.\.venv\Scripts\device-warden-tray
```

トレイはBrokerが動いていなければ自分で起動するため、実際には `device-warden-tray` だけで足ります。

ウィンドウを閉じると通知領域に格納されます。トレイメニューの「終了」はUIだけを終了します。Brokerは別途 `Ctrl+C` で停止してください。

### 単体のexeを作る

タスクバーには実行ファイル自身のアイコンが表示されるため、配布用にはexeをビルドします。

```powershell
.\scripts\build-exe.ps1
```

`dist\DeviceWarden.exe` が生成され、自動起動の登録もこのexeを優先して使います。

バージョンタグを作成すると、Windows向けビルドとテストが成功した場合にGitHub ReleasesへexeとSHA256チェックサムを公開するワークフローを用意しています。公開済みのファイルはリポジトリの「Releases」で確認できます。更新時は既存プロセスを終了してexeを差し替え、必要なら自動起動の登録を更新してください。設定・履歴は別ディレクトリに保持されます。

## 使い方

### 一覧画面

端末ごとに、名前・接続方法・ADB状態・最も目立つ利用可否・確保者・最終確認時刻を表示します。表示名は端末自身の情報から作ります。別名があればそれを使い、次にマーケティング名（`Redmi Pad SE 8.7`）、最後にブランドと型番（`OPPO CPH2699`）の順です。素の型番ばかりになることはありません。

操作は **自分が確保**、**解放**、**強制解放**（警告付き）、**AI許可切替**、**再確認**、**scrcpy**、**名前変更**、**表示切替**、**削除**、**履歴**、**自動起動** です。一覧とトレイアイコンは10秒ごとに更新されます。AIの確保・解放時はWindows通知が出ます。

**再確認** は選択端末のADB状態を取り直し、要確認の理由（接続切れ・ADB異常・Broker再起動・heartbeat停止）と検出時刻を確認する操作です。接続が戻ってもリースは自動解放されません。**scrcpy** は、自分が確保した接続中の端末でのみ起動し、PATH上の `scrcpy` を使います。起動しただけではリースは解放されません。

### 利用可否の表示

| 表示 | 意味 |
| --- | --- |
| **AI使用中** | AIがリース中で接続あり。自分の操作は避ける |
| **自分が使用中** | 自分がリース中 |
| **管理内では未確保** | Brokerにリースなし、ADB接続あり。管理外の利用は分からない |
| **管理内では未確保（ADB未接続）** | Brokerにリースなし、現在は未接続。物理的な利用可否は保証されない |
| **接続切れ・要確認** | リースはあるが接続が切れた。所有者を保持したまま |
| **管理内では未確保（ADB要確認）** | BrokerにリースなしだがADBが未承認または状態不明 |

AI利用禁止の端末は、人間はそのまま使え、AIポリシーだけが別表示になります。

### トレイアイコンの色

| 色 | 状態 |
| --- | --- |
| 緑 | 使える端末がある |
| 青 | 自分が確保中 |
| 黄 | AIが確保中 |
| 赤 | 要確認のリース、またはADB異常がある |
| 灰 | 端末なし、またはBroker未接続 |

### サインイン時の自動起動

```powershell
device-warden-autostart install   # 登録
device-warden-autostart status    # 確認
device-warden-autostart remove    # 解除
```

GUIの「自動起動」ボタンからも切り替えられます。現在のユーザーのスタートアップフォルダ（`%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Device Warden.lnk`）にショートカットを作るだけで、管理者権限は不要です。2回起動しても増えず、既存のウィンドウが前面に出ます。解除しても起動中のプロセスは止まりません。

## CLI

```powershell
device-warden list
device-warden claim --tool Codex                    # 空き端末を自動選択
device-warden claim --tool Codex --device <device_id>
device-warden adb <lease_id> shell getprop
device-warden heartbeat <lease_id>
device-warden release <lease_id>
device-warden audit
device-warden with-device --tool Codex -- shell getprop
```

`with-device` は指定のADBサブコマンドを1回だけBroker経由で実行します。確保に失敗すればADBを実行せず、成功時のみ解放します。ADBの失敗や中断時は安全のためリースを保持し、標準エラーへリースIDを出力します。この出力は秘密として扱い、作業状況を確認してから解放してください。強制終了などでIDが表示されない場合はGUIで所有者を確認してください。任意の子プロセスやGradleの仲介は対象外です。

仮想環境を有効化していない場合は、各コマンドの先頭に `.\.venv\Scripts\` を付けます。`--tool "Command Code"` のように任意のツール名を指定できます。

返ってくる `lease_id` が所有権の証明です。秘密として扱い、作業中は120秒以内の間隔でheartbeatを送り、終わったらreleaseしてください。AIのリースIDは `list` の出力には含めません。

## OpenCode との連携

OpenCode の設定にある `plugins` へ、このプラグインのディレクトリを追加します。

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "plugins": ["C:/apps/device-warden/integrations/opencode"]
}
```

`device_warden` ツールに `list` / `claim` / `heartbeat` / `release` / `adb` が生えます。`claim` すると呼び出し元のセッションID・タイトル・プロジェクト・作業ディレクトリをBrokerへ渡し、以降30秒ごとに自動でheartbeatを送ります。Brokerに接続できない場合は、黙って再試行せず、対処できるエラーを返します。

自動heartbeatが一度でも失敗した場合、そのセッションは次のツール呼び出しでエラーを受け取り、ADB操作や再確保ができなくなります。通信が戻っても自動再開・再確保はせず、作業状況と所有者を確認してから明示的にreleaseしてください。Brokerが停止中ならreleaseも失敗します。強制解放などでリースが既になくなっている場合はGUIの履歴を確認してください。プラグインの再読み込みやOpenCode再起動後は、このエラー状態は保持されません。

連携を成立させる前提は次のとおりです。

- 端末を操作する**前に** claim する。
- release は明示操作。セッション終了では解放しない。
- Brokerを経由しない直接の `adb` は、Device Warden の管理外。

## 仕組み

```text
┌──────────────────┐   localhost限定・トークン認証   ┌────────────────────┐
│ トレイ / CLI      │ ─────────────────────────────► │ Broker (127.0.0.1) │
│ OpenCode          │ ◄───────────────────────────── │ SQLite + ADB       │
└──────────────────┘                                └────────────────────┘
```

- `device_warden/store.py` — SQLiteスキーマ、リースのトランザクション、ADBの検出と仲介。
- `device_warden/server.py` — localhost限定のHTTP API、bearerトークン、定期監視。
- `device_warden/gui.py` — Tkinterの画面、トレイアイコン、状態色、前面化。
- `device_warden/autostart.py` — スタートアップのショートカット、単一インスタンス、前面表示の通知。
- `device_warden/icon.py` — アイコン描画（`assets/` のSVGが正本）。
- `integrations/opencode/` — OpenCode V2プラグインとそのテスト。

リースは自分の `lease_id` か、明示的な強制解放でのみ解放されます。切断・Broker再起動・heartbeat停止では `recovery_required` へ移り、空きには戻しません。USBとWi-Fiのシリアルは意図的に別端末として扱い、再接続を別の個体と取り違えないようにしています。

設計資料: [仕様書](docs/SPEC.md) · [技術設計](docs/ARCHITECTURE.md)

## 設定

| 変数 | 既定値 | 用途 |
| --- | --- | --- |
| `DEVICE_WARDEN_HOME` | `%USERPROFILE%\.device-warden` | DB、Brokerトークン、生成したアイコン |

データを `%LOCALAPPDATA%` に置かないのは意図的です。パッケージ版のPythonではAppDataが仮想化され、BrokerトークンがOpenCodeプラグインから見えなくなるためです。このディレクトリには端末シリアルやローカルパスが入るので、共有せず、バージョン管理にも入れないでください（`.gitignore` で除外済み）。

Windowsの継承ACLや別ユーザーからのアクセスに関する注意は [ローカルデータと権限](docs/WINDOWS-SECURITY.md) を参照してください。

## テスト

```powershell
python -m unittest discover -s tests -v
node integrations/opencode/test.mjs        # Node.js 24 以上
```

並列claim、ポリシー強制、切断・再接続時の所有者保持、heartbeat停止、ADB対象の固定、Broker認証、監査ログ、アイコンの状態優先度、自動起動の登録・解除を網羅しています。README用のスクリーンショットは `python scripts/capture-screenshot.py` で、架空の端末データから再生成できます。

## 制限

Device Warden は調整ツールであり、サンドボックスではありません。

- **直接のADB操作は検出も阻止もできない。** Brokerを通さずに `adb` を呼ぶ人やエージェントは、そのまま衝突し得る。この制約は仕様上の前提で、対策済みとは扱わない。
- **接続は到達性であって空き状態ではない。** 接続中の端末が物理的に使用中かもしれず、未接続の端末が物理的に空いているとも限らない。
- **シリアルの再利用や偽装は検出できない。** USBとWi-Fiの切り替えやシリアル変更は別端末として扱う。
- **ローカルでの隔離は限定的。** APIはlocalhost限定でトークン認証し、AIのリースIDは一覧に出さないが、同じWindowsユーザーで動くプロセスはトークンもDBも読める。
- **自動解放はしない。** アイドル検出やセッション終了で端末を解放することは、意図的に行わない。

## コントリビューション

Issue と Pull Request を歓迎します。

1. [仕様書](docs/SPEC.md) の状態モデルに従う。接続・リース・AIポリシーは別の軸として扱う。
2. 「接続断や失効したリースが黙って空きになる」挙動を弱めない。
3. 挙動を変えたらテストを追加し、上記のテストを通す。
4. 端末シリアル、IP、ローカルパス、セッション名、Brokerトークンをコミットしない。

## ライセンス

[MIT License](LICENSE) の下で公開しています。
