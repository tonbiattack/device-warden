# Store 提出用 MSIX の準備（Issue #39）

## 現在の状態

名前予約が完了し、正式な Identity Name で提出用 MSIX を生成できる状態になった。生成物は `dist\msix-store\DeviceWarden-store.msix`（署名済み、21.1 MB）。提出はまだしておらず、製品の状態は In draft。

## 確定している正式値

| 項目 | 値 |
| --- | --- |
| Store ID | `9P1HSTHZX5KH` |
| Identity Name | `tonbiattack.DeviceWarden` |
| Package Family Name | `tonbiattack.DeviceWarden_e28ksxe3fc9w4` |
| Publisher | `CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E` |
| PublisherDisplayName | `tonbiattack` |
| DisplayName（アプリ表示名） | `Device Warden`（PublisherDisplayName とは別物） |
| Version | `pyproject.toml` の version から4桁を生成（現在 `0.2.0.0`） |

`CN=2F14A66E-...` は開発者アカウント共通の Publisher で、秘密情報ではない。ZipScope / ZipSweep と同じ値。名前予約は約3か月で失効するため、その前に初回提出が必要。

## ビルド方法

```powershell
# 開発用（隔離。実データ・実端末を触らない。提出しない）
.\scripts\build-msix.ps1

# Store 用
.\scripts\build-msix.ps1 -Profile store -IdentityName tonbiattack.DeviceWarden `
  -CertificateThumbprint <Publisher と Subject が一致する証明書>
```

開発用と Store 用では**実行ファイル名が違う**。開発用は `DeviceWardenMsixDev.exe` で、この名前が隔離モード（専用データフォルダ・専用ポート・ADB 禁止）の目印になる。Store 用は `DeviceWarden.exe` で、通常のデータフォルダと ADB を使う。Store 用に開発用の名前を使うと、利用者の環境で ADB が使えないアプリになるため、テンプレートで分けている。

- 出力: `dist\msix-dev\DeviceWarden-dev-unsigned.msix` / `dist\msix-store\DeviceWarden-store-unsigned.msix`
- Publisher、PublisherDisplayName、表示名、Version は引数で上書きできる（`-Publisher`、`-PublisherDisplayName`、`-DisplayName`、`-Version`）
- `-Profile store` で `-IdentityName` を省略すると、**推測せずにエラーで止まる**。予約前の誤生成を防ぐため
- `-CertificateThumbprint <拇印>` を渡すと署名まで行う（未指定なら未署名のまま）

## 生成後に確認すること

```powershell
# マニフェストの値を確認する（展開して読む）
$pkg = 'dist\msix-store\DeviceWarden-store.msix'
$out = Join-Path $env:TEMP 'dw-store-check'
Remove-Item $out -Recurse -Force -ErrorAction SilentlyContinue
makeappx unpack /o /p $pkg /d $out
[xml]$m = Get-Content (Join-Path $out 'AppxManifest.xml')
$m.Package.Identity
$m.Package.Properties
```

確認項目:

- `Identity/Name` が Partner Center の「アプリ識別情報の表示」と一致する
- `Identity/Publisher` が `CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E`
- `Properties/PublisherDisplayName` が `tonbiattack`（アプリ表示名ではない）
- `Properties/DisplayName` と `VisualElements/DisplayName` が、予約した名前に一致する
- `Identity/Version` が4桁で、前回提出より大きい
- `StartupTask` が `Enabled="false"` のまま
- 署名した場合、`Get-AuthenticodeSignature` が `UnknownError`（自己署名で未信頼）でも、署名自体は付いている

## 生成物の実測結果（2026-11-27）

`DeviceWarden-store.msix` を展開して確認した値:

```
Name=tonbiattack.DeviceWarden
Publisher=CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E
PublisherDisplayName=tonbiattack
DisplayName=Device Warden
Version=0.2.0.0
Executable=DeviceWarden\DeviceWarden.exe
StartupTask Enabled=false
署名: 付与済み（自己署名のためローカルでは未信頼 = UnknownError）
サイズ: 21.1 MB
```

- レイアウト登録で `PackageFamilyName=tonbiattack.DeviceWarden_e28ksxe3fc9w4` を実測し、Partner Center の表示値と一致
- 同じ識別子で起動: GUI が表示され、Broker は通常の 47631 と `%USERPROFILE%\.device-warden` を使用。47632 と `.device-warden-msix-dev` は作られず、隔離モードに入らないことを確認
- WACK（パッケージパス指定）: 総合 **PASS**、任意の TEST88 のみ FAIL（adb / scrcpy / Broker を起動するための静的なプロセス起動参照）

## 署名について

Store は認定後に Microsoft の証明書で再署名するため、**証明書の購入・CA 証明書・`.pfx` の用意は不要**（[App package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements#code-signing-for-microsoft-store-submissions)、[Store readiness checklist](https://learn.microsoft.com/en-us/windows/apps/publish/store-readiness-checklist)）。

ただし、アップロードするパッケージには署名を付けておく。Store が「既存の署名を置き換える」形になり、未署名のアーカイブを渡さずに済む。Publisher と一致する Subject のローカル証明書でよい。この証明書をマシンに信頼させる必要は**ない**（Store 配布のみなら）。

```powershell
# 提出物用の署名証明書（秘密鍵はこのユーザーの Cert:\CurrentUser\My に残る）
$cert = New-SelfSignedCertificate -Type Custom -KeyUsage DigitalSignature `
  -Subject 'CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E' `
  -CertStoreLocation 'Cert:\CurrentUser\My' `
  -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')

.\scripts\build-msix.ps1 -Profile store -IdentityName tonbiattack.DeviceWarden -CertificateThumbprint $cert.Thumbprint
```

この証明書は Store 提出用で、他の配布には使わない。ローカルで**インストールして動かす**場合だけ、公開証明書をマシン範囲で信頼する（提出の必須手順ではない）。不要になった証明書は `Cert:\CurrentUser\My` から削除する。

## 再提出・更新のとき

1. `pyproject.toml` の `version` を上げる（4桁の最後は 0 のまま。Store が管理する）
2. `-Profile store -IdentityName tonbiattack.DeviceWarden -CertificateThumbprint <拇印>` でビルドする
3. 上の確認項目を実測し、結果を Issue に記録する
4. パッケージパス指定の WACK を実行する
5. Store の掲載情報（プライバシーポリシー URL、スクリーンショット、年齢レーティング）を用意し、Partner Center へ提出する（Muse 側）

4. Store の掲載情報（プライバシーポリシー URL、スクリーンショット、年齢レーティング）を用意し、Partner Center へ提出する（Muse 側）
