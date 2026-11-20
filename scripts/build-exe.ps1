# Build a Windows executable whose own icon is the Device Warden shield.
#
# pythonw.exe keeps Python's icon in the taskbar regardless of what the window
# sets, so distribution builds use this launcher instead. See README.md.

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

# `py` is the Windows launcher and is not installed everywhere (it is missing on
# this machine and on some CI images), so fall back to `python` on PATH.
$python = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' } else { 'python' }
if (-not (Get-Command $python -ErrorAction SilentlyContinue)) {
  throw 'Pythonが見つかりません。PATHを確認してください。'
}

& $python -c "from device_warden.icon import image; image('free', 256).save('assets/app-icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE -ne 0) { throw 'アイコンの生成に失敗しました。' }

& $python -m venv .build-venv
if ($LASTEXITCODE -ne 0) { throw 'ビルド環境の作成に失敗しました。' }
.\.build-venv\Scripts\python -m pip install --quiet --upgrade pip
if ($LASTEXITCODE -ne 0) { throw 'pipの更新に失敗しました。' }
.\.build-venv\Scripts\python -m pip install --quiet -e . pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'ビルド依存関係の導入に失敗しました。' }

.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
  --name DeviceWarden `
  --icon assets\app-icon.ico `
  --collect-submodules device_warden `
  launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstallerのビルドに失敗しました。' }

Write-Host "Built: dist\DeviceWarden.exe"
