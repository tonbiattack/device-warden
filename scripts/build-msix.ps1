# Build an MSIX for local checking or for a Store submission.
#
# The development profile is isolated from real data and is never submitted.
# The store profile still needs the reserved Identity Name from Partner Center.
[CmdletBinding()]
param(
    [ValidateSet('dev', 'store')]
    [string]$Profile = 'dev',
    [string]$IdentityName = '',
    [string]$Publisher = '',
    [string]$PublisherDisplayName = '',
    [string]$DisplayName = '',
    [string]$Version = '',
    [string]$CertificateThumbprint = '',
    [string]$Python = '',
    [string]$MakeAppx = '',
    [string]$SignTool = '',
    [string]$OutputRoot = ''
)
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not $OutputRoot) {
    $OutputRoot = if ($Profile -eq 'store') { 'dist\msix-store' } else { 'dist\msix-dev' }
}
if (-not $Python) { $Python = Join-Path (Get-Location).Path '.build-venv\Scripts\python.exe' }
if (-not $MakeAppx) {
    $sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    $found = Get-ChildItem $sdk -Filter makeappx.exe -Recurse -ErrorAction Stop |
        Where-Object { $_.Directory.Name -eq 'x64' } |
        Sort-Object { $_.Directory.Parent.Name -as [version] } -Descending | Select-Object -First 1
    if (-not $found) { throw 'Windows SDK の makeappx.exe が見つかりません。' }
    $MakeAppx = $found.FullName
}
if (-not (Test-Path $Python)) { throw 'ビルド用 Python がありません。.build-venv に依存関係を導入してください。' }
$root = [IO.Path]::GetFullPath((Join-Path (Get-Location).Path $OutputRoot))
# A unique layout prevents stale binaries or unrelated personal files from
# entering a later package. Keep it under OutputRoot for manual inspection.
New-Item -ItemType Directory -Force $root | Out-Null
$layout = Join-Path $root ('layout-' + [guid]::NewGuid().ToString('N'))
$assets = Join-Path $layout 'Assets'
New-Item -ItemType Directory -Force $assets | Out-Null

# Render the manifest before packing so the identity is explicit in the output.
$renderArgs = @('-m', 'device_warden.msixmanifest', '--profile', $Profile,
    '--template', 'msix\AppxManifest.xml', '--output', (Join-Path $layout 'AppxManifest.xml'))
foreach ($pair in @(@('identity-name', $IdentityName), @('publisher', $Publisher),
        @('publisher-display-name', $PublisherDisplayName), @('display-name', $DisplayName),
        @('version', $Version))) {
    if ($pair[1]) { $renderArgs += "--$($pair[0])"; $renderArgs += $pair[1] }
}
& $Python @renderArgs
if ($LASTEXITCODE -ne 0) { throw 'マニフェストの生成に失敗しました。' }

# Generate the EXE icon in the fresh layout rather than depending on the normal
# build's generated, potentially missing assets/app-icon.ico.
$icon = Join-Path $root ('app-icon-' + [guid]::NewGuid().ToString('N') + '.ico')
$env:DW_MSIX_EXE_ICON = $icon
try {
    & $Python -c "import os; from device_warden.icon import image; image('free',256).save(os.environ['DW_MSIX_EXE_ICON'],sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if ($LASTEXITCODE -ne 0) { throw 'EXE アイコンの生成に失敗しました。' }
} finally { Remove-Item Env:\DW_MSIX_EXE_ICON -ErrorAction SilentlyContinue }
$exeManifest = (Resolve-Path 'msix\exe.manifest').Path
# The package is self-contained, so a one-folder build avoids the one-file
# self-extraction that WACK flags and keeps startup independent of TEMP.
& $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name DeviceWardenMsixDev `
    --icon $icon --manifest $exeManifest --collect-submodules device_warden `
    --distpath $layout --workpath (Join-Path $root 'pyinstaller') `
    --specpath (Join-Path $root 'pyinstaller') launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller のビルドに失敗しました。' }
$env:DW_MSIX_ASSETS = $assets
try {
    & $Python -c "import os; from pathlib import Path; from device_warden.icon import image; p=Path(os.environ['DW_MSIX_ASSETS']); [(image('free', size).save(p/name)) for name,size in [('StoreLogo.png',50),('Square150x150Logo.png',150),('Square44x44Logo.png',44)]]"
    if ($LASTEXITCODE -ne 0) { throw 'MSIX アイコンの生成に失敗しました。' }
} finally { Remove-Item Env:\DW_MSIX_ASSETS -ErrorAction SilentlyContinue }
$package = Join-Path $root "DeviceWarden-$Profile-unsigned.msix"
& $MakeAppx pack /o /d $layout /p $package
if ($LASTEXITCODE -ne 0) { throw 'MSIX の作成に失敗しました。' }

# The Store re-signs the package, so its certificate does not need to be trusted
# here. Signing keeps the upload a normal MSIX and lets the Store replace a
# signature instead of seeing an unsigned archive.
if ($CertificateThumbprint) {
    if (-not $SignTool) {
        $sdkBin = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
        $found = Get-ChildItem $sdkBin -Filter signtool.exe -Recurse -ErrorAction Stop |
            Where-Object { $_.Directory.Name -eq 'x64' } |
            Sort-Object { $_.Directory.Parent.Name -as [version] } -Descending | Select-Object -First 1
        if (-not $found) { throw 'Windows SDK の signtool.exe が見つかりません。' }
        $SignTool = $found.FullName
    }
    & $SignTool sign /fd SHA256 /sha1 $CertificateThumbprint $package
    if ($LASTEXITCODE -ne 0) { throw 'MSIX への署名に失敗しました。' }
    $signed = Join-Path $root "DeviceWarden-$Profile.msix"
    Move-Item $package $signed -Force
    $package = $signed
    Get-AuthenticodeSignature $package | Select-Object Status, StatusMessage | Format-List | Out-Host
}
Write-Host "MSIX: $package"
if ($Profile -eq 'dev') {
    Write-Host '検証用です。開発者モードか証明書の信頼が必要で、Store に提出しないでください。'
} else {
    Write-Host 'Store は認定後に Microsoft の証明書で再署名します。提出前にマニフェストの値を確認してください。'
}
