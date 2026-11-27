# Safe cleanup helpers for verification scripts.
#
# A 2026-11-27 incident deleted parts of the user profile because a script used
# `$home` as a temporary variable name; `$HOME` is read-only, so the variable
# kept pointing at C:\Users\<user> and the cleanup removed the whole tree.
# Recursive deletes now go through these functions, which only accept a path
# inside %TEMP% and refuse the TEMP root, wildcards and reparse points.

Set-StrictMode -Version Latest

function Get-NormalizedFullPath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    if ([string]::IsNullOrWhiteSpace($Path)) { throw 'パスが空です。' }
    if ($Path -match '[*?]') { throw "ワイルドカードは使用できません: $Path" }
    $full = [IO.Path]::GetFullPath($Path)
    # Keep a drive root ("C:\") intact; trim separators from normal paths.
    if ($full.Length -gt 3) { $full = $full.TrimEnd([char[]]@('\', '/')) }
    return $full
}

function Test-IsUnderPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Path,
        [Parameter(Mandatory)][string]$Parent
    )

    $child = (Get-NormalizedFullPath $Path).ToLowerInvariant()
    $root = (Get-NormalizedFullPath $Parent).ToLowerInvariant()
    if ($root.EndsWith('\')) { return $child.StartsWith($root) }
    return $child.StartsWith($root + '\')
}

function Assert-SafeCleanupPath {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    $full = Get-NormalizedFullPath $Path
    $temp = Get-NormalizedFullPath $env:TEMP
    if ($full.ToLowerInvariant() -eq $temp.ToLowerInvariant()) {
        throw "TEMP 自体は削除できません: $full"
    }
    if (-not (Test-IsUnderPath $full $temp)) {
        throw "TEMP の外は削除できません: $full"
    }
    if (Test-Path -LiteralPath $full) {
        $item = Get-Item -LiteralPath $full -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "再解析ポイントは削除できません: $full"
        }
    }
    return $full
}

function New-SafeTempDir {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Purpose)

    $clean = $Purpose -replace '[^A-Za-z0-9-]', '-'
    if ([string]::IsNullOrWhiteSpace($clean)) { $clean = 'work' }
    $path = Join-Path $env:TEMP ("dw-$clean-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $path -ErrorAction Stop | Out-Null
    return $path
}

function Remove-SafeTempDir {
    [CmdletBinding()]
    param([Parameter(Mandatory)][string]$Path)

    $full = Assert-SafeCleanupPath $Path
    if (-not (Test-Path -LiteralPath $full)) { return }
    Remove-Item -LiteralPath $full -Recurse -Force -ErrorAction Stop
}

Export-ModuleMember -Function Get-NormalizedFullPath, Test-IsUnderPath,
    Assert-SafeCleanupPath, New-SafeTempDir, Remove-SafeTempDir
