# Self-test for scripts/Safety.psm1.
#
# The unsafe cases matter more than the happy path: they are the ones that
# would delete a user profile if the guard were wrong.

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'Safety.psm1') -Force

$failures = [System.Collections.Generic.List[string]]::new()

function Expect-Blocked {
    param([string]$Name, [scriptblock]$Action)
    try {
        & $Action
        $failures.Add("$Name`: ブロックされませんでした")
    } catch {
        Write-Host "OK blocked: $Name"
    }
}

# The happy path: a generated temporary directory can be removed.
$temp = New-SafeTempDir -Purpose 'safety-test'
Set-Content -LiteralPath (Join-Path $temp 'probe.txt') -Value 'probe'
if (-not (Test-IsUnderPath $temp $env:TEMP)) { $failures.Add('生成先が TEMP の外です') }
Remove-SafeTempDir -Path $temp
if (Test-Path -LiteralPath $temp) { $failures.Add('一時フォルダが残っています') } else { Write-Host 'OK removed: generated temp dir' }

$sibling = New-SafeTempDir -Purpose 'safety-test'
if ($sibling -eq $temp) { $failures.Add('同じパスが再生成されました') }
Remove-SafeTempDir -Path $sibling

# Paths that must never be touched.
Expect-Blocked 'TEMP 自体' { Remove-SafeTempDir -Path $env:TEMP }
Expect-Blocked 'ユーザープロファイル' { Remove-SafeTempDir -Path $env:USERPROFILE }
Expect-Blocked 'ドライブ直下' { Remove-SafeTempDir -Path 'C:\' }
Expect-Blocked 'TEMP の外' { Remove-SafeTempDir -Path (Join-Path $env:USERPROFILE 'Documents\dw-unsafe-probe') }
Expect-Blocked 'ワイルドカード' { Remove-SafeTempDir -Path (Join-Path $env:TEMP 'dw-*') }
Expect-Blocked '空文字' { Remove-SafeTempDir -Path '' }

# A junction inside TEMP must be refused even though its path looks safe.
$target = New-SafeTempDir -Purpose 'safety-target'
$link = Join-Path (New-SafeTempDir -Purpose 'safety-link') 'junction'
try {
    New-Item -ItemType Junction -Path $link -Target $target -ErrorAction Stop | Out-Null
    Expect-Blocked '再解析ポイント' { Remove-SafeTempDir -Path $link }
} catch {
    Write-Host "SKIP junction test: $($_.Exception.Message)"
} finally {
    Remove-SafeTempDir -Path (Split-Path -Parent $link)
    Remove-SafeTempDir -Path $target
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    exit 1
}
Write-Host 'Safety self-test passed.'
exit 0
