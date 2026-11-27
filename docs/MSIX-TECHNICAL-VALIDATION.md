# MSIX 技術検証（Issue #31、2026-11-27）

## 結論

**開発者モードを有効にしたこのマシンで、隔離した開発用 MSIX の登録・起動・永続化・アンインストールまで確認できた。** ただし署名済みパッケージのインストール、実端末のリース、Store 提出は未達・未検証。

試作は正式な Store 版ではない。マニフェストの ID、発行元、表示名、バージョンは仮値で、Partner Center の正式 ID と一致しない。署名済み `.msix` は自己署名で、このマシンではまだ信頼されていない。配布・提出には使わない。

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python -m pip install -e . pyinstaller
.\scripts\build-msix.ps1
```

`dist/msix-dev/DeviceWarden-dev-unsigned.msix` に未署名の検証用パッケージを生成する。登録は開発者モードで行う。

```powershell
# 開発者モード（管理者）。0 に戻せば元へ戻る。
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" `
  /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f

# 生成した layout を登録する（未署名のまま）
Add-AppxPackage -Register dist\msix-dev\layout-*\AppxManifest.xml
```

## パッケージ構成

one-folder（`--onedir`）でビルドし、`DeviceWardenMsixDev\DeviceWardenMsixDev.exe` を起動対象にしている。one-file は起動のたびに自分自身を一時ディレクトリへ展開するため、読み取り専用のパッケージには向かない。one-folder なら展開も自己再実行も無く、WACK の指摘も exe と Python 本体の静的なプロセス起動参照だけになる（TEST88、任意）。

## 実測した結果

環境: Windows 11 Pro 26300、Windows SDK / WACK 10.0.26100.9457、Python 3.13。

| 項目 | 結果 |
| --- | --- |
| パッケージ生成 | MakeAppx で成功（未署名）。one-folder 構成で、exe と `_internal` を含む約21MB。個人データやトークンは含まない。 |
| 登録 | 開発者モード有効化後、未署名レイアウトの `Add-AppxPackage -Register` が成功。`Tonbiattack.DeviceWarden.Dev_0.2.0.0_x64__f66yps8pfc6q8`、Status は `Ok`。 |
| 起動 | スタートメニュー項目「Device Warden (Development)」から起動でき、GUI ウィンドウ「Device Warden」が表示された。プロセスは GUI と `--broker` の2つ（one-folder なので one-file の自己展開プロセスが無い）。 |
| 単一インスタンス | 起動中にもう一度起動してもプロセス数と起動時刻は変わらず、既存ウィンドウが残った（二重起動しない）。 |
| 隔離 | パッケージから起動した Broker は **47632 のみ**を待ち受け、専用 DB（`~/.device-warden-msix-dev/warden.db`）を使った。一覧は空、`claim` は「MSIX検証版では端末の確保を無効化しています」で拒否。通常の Broker（47631）はそのまま稼働を続けた。 |
| exe 隣への書込み | 実行後も layout に増えたのは Windows が作る `microsoft.system.package.metadata` だけで、DB・アイコン・ログは作られなかった。生成物はすべて `DEVICE_WARDEN_HOME` 側。 |
| 永続化 | 起動中に `default-policy` で書いた設定（`default_ai_allowed=0`）が、プロセス停止・再起動後も DB に残った。DB は 65536 バイト。 |
| loopback とトークン共有 | パッケージ内の Broker へ、**パッケージ外の通常 Python クライアント**がトークン認証付きで接続できた。full-trust ではこの向きの loopback と `%USERPROFILE%` のトークン共有が通る。 |
| 自動起動 | `desktop:StartupTask` は無効のまま。既存の Startup フォルダのショートカット（通常版 `dist\DeviceWarden.exe`、2026-11-14 作成）は変更されず、MSIX 版が二重登録を作らなかった。 |
| アンインストール | パッケージ、スタートメニュー項目、プロセスが消え、47632 も閉じた。専用データは**パッケージ外なので残る**（設計どおり）。強制終了したため `broker.token` が残ったが、次回起動時に置き換わる。通常版の Broker とショートカットは無傷。 |
| 署名済み MSIX のインストール | `0x800B0109`（ルート証明書が未信頼）。CurrentUser の TrustedPeople へ一時追加しても同じで、追加は直後に削除した。マシン範囲の信頼（管理者）が必要。 |
| WACK（パッケージパス指定） | 総合 **PASS**。任意の TEST88（ブロック済み実行可能ファイル）は、exe と `python313.dll` が CreateProcessW / ShellExecuteW を参照しているため **FAIL**。Broker・adb・scrcpy を起動する設計上、静的な参照は避けられない。DPI の WARNING は PerMonitorV2 宣言で解消した。 |
| WACK（インストール済み） | 管理者権限で `-packagefullname` を実行したが、**全項目 FAIL**。メッセージは「このアプリ パッケージのマニフェスト ファイルが見つかりませんでした」「パスの形式が無効です」で、レイアウト登録（WindowsApps ではない）のパッケージを WACK が解決できないため。アプリの不具合ではなく、正式インストール（署名済み）で再実行する必要がある。 |
| Android 端末 | 検証時点では実機・エミュレーターとも未接続。ADB は試作が拒否する設計のため、実端末の claim / heartbeat / release は未検証。 |

## 未検証と制約

- **ADB と claim の拒否は試作品だけの性質**で、MSIX 化そのものの制限ではない。正式版は `DEVICE_WARDEN_HOME`・ポート・端末を通常版と共有するため、二重 DB の問題は起きない（同じポートなので Broker は1つだけ）。試作で隔離したのは、検証中に実データと実端末を触らないためである。
- **署名済みパッケージの配布経路**は未検証。マシン範囲の証明書信頼か、正式なコード署名証明書が必要。信頼変更は同じ発行元の他のパッケージにも影響し得る。
- レイアウト登録ではアプリが**ソースフォルダから実行**される。WindowsApps 配下の読み取り専用パッケージでの挙動（更新・修復・仮想化）は未検証。今回確認できたのは「exe の隣に書かない」ところまで。
- 逆向きの loopback（パッケージから外部 Broker へ）は、隔離のため別ポートを使っており未検証。
- トレイアイコンの目視、通知、高 DPI の実際の表示品質は未確認。
- StartupTask の有効化、ユーザーによる無効化の尊重、サインアウト／再サインイン後の起動は未検証。
- 実端末を使う検証は、隔離した二つ目の DB が同じ端末を「空き」と誤認するため行っていない。通常 Broker と競合しない方式を決めてから実施する。
- Partner Center の正式 ID、証明書、Store 申告、S モードや外部 `adb` 依存の扱いは未対応。

## 保存先の考え方

通常版は `DEVICE_WARDEN_HOME` がなければ `%USERPROFILE%\.device-warden` に DB・トークン・生成アイコンを置く。外部 CLI / MCP / OpenCode と同じトークンを見られることが要件で、今回の実測でもパッケージ外のクライアントが同じトークンで接続できた。

[コンテナ化の概要](https://learn.microsoft.com/en-us/windows/msix/msix-containerization-overview)では、full-trust アプリのユーザープロファイルへの書き込みは通常そのまま通ると説明されている。今回の実測はこれと矛盾しない。パッケージ内は読み取り専用で、PyInstaller の one-file 展開は一時ディレクトリを使う。

## 自動起動・常駐

`desktop:StartupTask` は **既定無効**で宣言する。インストール直後に同意なくサインイン時起動を有効にしないためで、有効化はユーザーが Windows の設定から行う。MSIX 版は従来の Startup フォルダのショートカットを書かず、GUI は「設定 → アプリ → スタートアップ」を案内する。既存の通常版ショートカットが残っている場合、両方が起動し得るため移行時に所有者が確認する。詳細は [StartupTask の仕様](https://learn.microsoft.com/en-us/uwp/schemas/appxpackage/uapmanifestschema/element-desktop-startuptask)。

## localhost と外部依存

Broker は 127.0.0.1 のトークン認証 API。今回、パッケージ内 Broker への外部クライアント接続は通ったが、これは full-trust の実測であって Store 審査や他環境を保証しない。loopback 免除（`CheckNetIsolation`）は管理者権限が必要で、Store 配布の手段としては使わない。必要になった場合は適用範囲と撤去を決めてから行う。

ADB（Android SDK Platform-Tools）は必須、scrcpy と apksigner は利用機能に応じて別途必要。MSIX はそれらを同梱しない。PATH とパッケージ化プロセスからの外部プログラム起動、ライセンス、Store の申告・S モードの制約は別途確認する。

## 残作業

1. 署名済みパッケージを配布するなら、正式なコード署名証明書を用意し、マシン範囲の信頼（管理者）でインストールを再検証する。検証用証明書の公開証明書だけを信頼する場合の撤去手順も決める。
2. 実機検証を通常 Broker と競合させない方式（例: 通常版だけを使い、MSIX 版は UI と常駐の確認に限定する）を決める。
3. 管理者権限のアクティブセッションで、**署名済みの正式インストール**に対して WACK を実行する。レイアウト登録のままでは全項目 FAIL になり、判定材料にならない。任意 TEST88 の FAIL は静的参照の指摘なので、実行時の子プロセス起動（Broker、adb、scrcpy）とあわせて評価する。
4. StartupTask の有効化とサインイン時起動、高 DPI、トレイ通知を実機で確認する。
5. Partner Center の正式 ID・証明書・申告情報を用意し、提出用パッケージを作り直す。
