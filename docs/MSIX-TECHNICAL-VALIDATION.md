# MSIX 技術検証（Issue #31、2026-11-27）

## 結論

これは **Store 提出版ではなく、実機・実データから隔離した x64 の試作**。MakeAppx によるパッケージ作成までは成功したが、インストール・実行の受け入れ条件は未達。マニフェストの ID、発行元、表示名、バージョンは仮値で、Partner Center から割り当てられる正式な ID と一致しない。署名されていないビルド成果物は配布・提出しない。

試作の準備例（Python 3.11 以上、Windows SDK が必要）:

```powershell
python -m venv .build-venv
.\.build-venv\Scripts\python -m pip install -e . pyinstaller
.\scripts\build-msix.ps1
```

`dist/msix-dev/DeviceWarden-dev-unsigned.msix` に未署名の検証用パッケージを生成する。

試作は独立した `%USERPROFILE%\.device-warden-msix-dev` とポート 47632 を使用し、ADB と端末の確保を禁止する。判別は実行ファイル名だけでなく、パッケージ識別名（`...Dev_...`）でも行う。実行ファイル名を変えても隔離が外れないようにするためである。**本物の端末を二つの独立した DB で管理すると排他が効かない**ため、この制約は意図的である。したがってこの試作は実機の claim / heartbeat / release や ADB 連携を検証できない。試作データもトークンを含むため公開しない。正式版は既存 Broker との安全な共存方法を検証してから決める。

## 観察した結果

| 項目 | 結果 |
| --- | --- |
| 開発版 MSIX の作成 | `scripts/build-msix.ps1` と Windows SDK MakeAppx で成功。出力は未署名。 |
| パッケージ内容 | `makeappx unpack` で展開でき、中身は `AppxManifest.xml`・`AppxBlockMap.xml`・exe・タイル画像3枚の計6ファイルのみ。個人データやトークンは含まれない。 |
| 試作 exe の隔離 | **非 MSIX として** Broker を起動し、通常 Broker の 47631 と試作の 47632 が別プロセスで動作した。子プロセスへ `DEVICE_WARDEN_*` を一切渡さなくても、試作は専用 DB を使い一覧は空、`claim` は拒否した。MSIX の仮想化や loopback の証明ではない。 |
| 未署名レイアウト登録 | `Add-AppxPackage -Register` が `0x80073CFF` で拒否。開発者モード未有効。 |
| 署名付き試作のインストール | 自己署名の検証用パッケージで `0x800B0109`。証明書はマシンで信頼されていない。CurrentUser の TrustedPeople に一時追加しても同じ結果、追加は直後に削除した。 |
| WACK | 署名付きの最新試作に対し WACK 10.0.26100.9457 を非管理者で実行。DPI マニフェスト追加後、XML の総合結果は **PASS**、23 項目 PASS。ただし任意の TEST88 は **FAIL**（CreateProcessW と実行可能ファイル名への静的参照）で、文字どおりの全項目 PASS ではない。高 DPI の実際の表示品質は未検証。 |
| CI | `.github/workflows/msix.yml` が同じビルドと内容検査（ID・実行ファイル名・StartupTask 無効）を行う。ローカルで同じ検査手順が通ることは確認済み。 |
| Android 端末 | 検証時点では実機・エミュレーターとも未接続。 |
| 再起動とアンインストール | MSIX を登録できず未検証。 |

WACK は管理者権限での実行が公式手順であり、今回の結果だけで Store 認証の可否を断定しない。レポートや試験パッケージは `dist/` に生成されるローカル試験成果物で、公開しない。

## 保存先と互換性

通常版は `DEVICE_WARDEN_HOME` がなければ `%USERPROFILE%\.device-warden` に DB・トークン・生成アイコンを置く。外部 CLI / MCP / OpenCode と同じトークンを見られることが要件。パッケージ内は読み取り専用であり、生成ファイルを実行ファイルの隣には置かない。PyInstaller の one-file 展開は一時ディレクトリを使う。MSIX 版で実際に同一パスへ読み書き・再起動後に復元・アンインストール後に期待どおり残るかは未検証。移行や削除を勝手に行わない。

[Microsoft のパッケージ化デスクトップアプリの説明](https://learn.microsoft.com/en-us/windows/msix/desktop/desktop-to-uwp-behind-the-scenes)では、パッケージ内容は読み取り専用で、仮想化対象や AppData の挙動はアプリの種類により異なる。[コンテナ化の概要](https://learn.microsoft.com/en-us/windows/msix/msix-containerization-overview)では、full-trust アプリのユーザープロファイルへの書き込みは通常そのまま通ると説明されている。`%USERPROFILE%` 配下の動作を推測せず、**パッケージ起動プロセスと通常プロセスの両側から実測する**。`DEVICE_WARDEN_HOME` の外部指定先についても同様。

## 自動起動・常駐

試作は MSIX の `desktop:StartupTask` を **既定無効**で宣言する。インストール直後に同意なくサインイン時起動を有効にしないためで、有効化はユーザーが Windows の設定から行う。MSIX 版から従来の Startup フォルダのショートカットを書かないようにし、GUI は Windows の「設定 → アプリ → スタートアップ」を案内する。実際の有効化、ユーザーによる無効化の尊重、サインアウト／再サインイン後の動作は未検証。既存の通常版ショートカットがあれば、移行時に両方起動しないよう所有者が確認する。詳細は [StartupTask の仕様](https://learn.microsoft.com/en-us/uwp/schemas/appxpackage/uapmanifestschema/element-desktop-startuptask)。トレイの表示・二重起動・前面化も MSIX 上では未確認。

## localhost と外部依存

Broker は 127.0.0.1 のトークン認証 API。試作の非 MSIX Broker との通信成功、および試作 Broker が実端末の claim を拒否することは確認したが、MSIX 版の疎通を意味しない。Microsoft の [IPC の説明](https://learn.microsoft.com/en-us/windows/apps/develop/communication/interprocess-communication) は packaged アプリの loopback と例外を説明しているが、full-trust アプリの実際の組み合わせはこの環境で確認できていない。**免除は成功の前提と決めつけず**、パッケージ化されたトレイ・Broker と通常の CLI / MCP / OpenCode の各方向を計測する。免除が必要なら管理者権限・配布可能性・適用範囲を確認する。無断で `CheckNetIsolation` の設定は変更しない。

ADB（Android SDK Platform-Tools）は必須、scrcpy と apksigner は利用機能に応じて別途必要。MSIX はそれらを同梱しない。PATH とパッケージ化プロセスからの外部プログラム起動、ライセンス、Store の申告・S モードの制約は別途確認する。「別途 adb が必要」と表示するだけで Store 審査を満たすかは未確認。

## 管理者権限が必要な次の一手

インストール検証は、次のどちらかを**管理者が明示的に**行うまで進められない。どちらもマシンの信頼設定を変えるため、実施範囲・期限・撤去を決めてから行う。

```powershell
# A. 開発者モードを有効にする（管理者）。0 に戻せば元へ戻る。
reg add "HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock" `
  /v AllowDevelopmentWithoutDevLicense /t REG_DWORD /d 1 /f
# 元に戻す: /d 0
```

```powershell
# B. 検証用証明書の「公開証明書のみ」を信頼する（管理者）。
#    秘密鍵（.pfx）は配布も共有もしない。
Import-Certificate -FilePath .\dev-signing.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople
# 撤去: 拇印を確認して削除する
Get-ChildItem Cert:\LocalMachine\TrustedPeople |
  Where-Object Thumbprint -eq '<thumbprint>' | Remove-Item
```

B は同じ発行元の他のパッケージにも影響し得る。A は開発者モードを常時有効にする。どちらも、この試作だけを入れて消す目的なら**検証後に戻す**。

## 検証を再開するために必要なこと

1. 管理者が Windows の開発者モードを有効にするか、適切な検証用コード署名証明書の公開証明書をマシンの TrustedPeople に登録する（上の手順）。信頼変更は他のパッケージにも影響し得るため、期限・対象・撤去手順を確認する。秘密鍵は共有しない。
2. 試作と別に、**実機テスト時は通常 Broker と競合しない設計**を準備し、ユーザーの許可の下で実端末の確保、heartbeat、解放を試す。
3. パッケージからの起動・トレイ・二重起動、通常 CLI / MCP / OpenCode の疎通、再起動後の DB・トークン、StartupTask、アンインストール後のデータを検証する。
4. 管理者権限かつアクティブなユーザーセッションで [WACK の公式手順](https://learn.microsoft.com/en-us/windows/uwp/debug-test-perf/windows-app-certification-kit)を実施し、XML の各結果を確認する。任意 TEST88 の FAIL を調査し、全項目 PASS と Store 審査可否を別々に判断する。
5. Partner Center の正式な package identity・証明書・申告情報を用意してから提出用パッケージを作り、再検証する。
