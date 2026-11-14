# Build a Windows executable whose own icon is the Device Warden shield.
#
# pythonw.exe keeps Python's icon in the taskbar regardless of what the window
# sets, so distribution builds use this launcher instead. See README.md.

$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)

python -c "from device_warden.icon import image; image('free', 256).save('assets/app-icon.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

py -m venv .build-venv
.\.build-venv\Scripts\python -m pip install --quiet --upgrade pip
.\.build-venv\Scripts\python -m pip install --quiet -e . pyinstaller

.\.build-venv\Scripts\pyinstaller --noconfirm --clean --onefile --windowed `
  --name DeviceWarden `
  --icon assets\app-icon.ico `
  --collect-submodules device_warden `
  launcher.py

Write-Host "Built: dist\DeviceWarden.exe"
