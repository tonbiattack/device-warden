# Store 提出用 MSIX の準備（Issue #39）

## 現在の状態

マニフェストはテンプレートになり、開発用と Store 用の識別情報を**引数で切り替える**形にした。正式な Identity Name は Partner Center の名前予約待ちなので、**まだ提出用 MSIX は生成しない**。予約後にコメントで連絡があれば、下のコマンド1本で切り替えられる。

## 確定している正式値

| 項目 | 値 | 状態 |
| --- | --- | --- |
| Publisher | `CN=2F14A66E-02DF-43E2-93E0-5C5900B38D2E` | 確定（アカウント共通） |
| PublisherDisplayName | `tonbiattack` | 確定 |
| DisplayName（アプリ表示名） | `Device Warden` | 確定（PublisherDisplayName とは別物） |
| Identity Name | `tonbiattack.DeviceWarden` 見込み | **未確定（予約後）** |
| Package Family Name | Identity Name から決まる | 未確定 |
| Store ID | — | 未確定 |
| Version | `pyproject.toml` の version から4桁を生成 | 自動 |

`CN=2F14A66E-...` は開発者アカウント共通の Publisher で、秘密情報ではない。ZipScope / ZipSweep と同じ値。

## ビルド方法

```powershell
# 開発用（隔離。実データ・実端末を触らない。提出しない）
.\scripts\build-msix.ps1

# Store 用（Identity Name の確定後）
.\scripts\build-msix.ps1 -Profile store -IdentityName tonbiattack.DeviceWarden
```

- 出力: `dist\msix-dev\DeviceWarden-dev-unsigned.msix` / `dist\msix-store\DeviceWarden-store-unsigned.msix`
- Publisher、PublisherDisplayName、表示名、Version は引数で上書きできる（`-Publisher`、`-PublisherDisplayName`、`-DisplayName`、`-Version`）
- `-Profile store` で `-IdentityName` を省略すると、**推測せずにエラーで止まる**。予約前の誤生成を防ぐため

## 生成後に確認すること

```powershell
# マニフェストの値を確認する（展開して読む）
$pkg = 'dist\msix-store\DeviceWarden-store-unsigned.msix'
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
- `Properties/DisplayName` と `VisualElements/DisplayName` が `Device Warden`
- `Identity/Version` が4桁で、前回提出より大きい
- `StartupTask` が `Enabled="false"` のまま

## 署名について

Store は認定後に Microsoft の証明書で再署名するため、証明書の購入・CA 証明書・`.pfx` は不要（[App package requirements](https://learn.microsoft.com/en-us/windows/apps/publish/publish-your-app/msix/app-package-requirements#code-signing-for-microsoft-store-submissions)）。

ローカルで署名済みパッケージを試す場合だけ、検証用証明書をマシン範囲で信頼する必要がある。提出の必須手順ではない。

## 未確定事項が確定した後の手順

1. `-Profile store -IdentityName <予約名>` でビルドする
2. 上の確認項目を実測し、結果を Issue #39 に記録する
3. パッケージパス指定の WACK を実行する（署名済みならインストール済み WACK も可）
4. Partner Center へ提出する（Muse 側）
