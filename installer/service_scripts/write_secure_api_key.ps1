# N034: CSPRNG api_key.txt + restrictive DACL (SYSTEM / Administrators / LocalService).
# Uses icacls.exe (not Get-Acl/Set-Acl) for runner/module portability.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File write_secure_api_key.ps1 -Path <file>
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"

function Invoke-Icacls {
    param([Parameter(Mandatory = $true)][string[]]$Args)
    & icacls.exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed ($LASTEXITCODE): icacls $($Args -join ' ')"
    }
}

$dir = Split-Path -Parent -Path $Path
if (-not [string]::IsNullOrWhiteSpace($dir) -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
[System.IO.File]::WriteAllText($Path, $hex + [Environment]::NewLine)

# Restrictive DACL: no inherited Everyone/Users; only SYSTEM/Administrators/LocalService read.
Invoke-Icacls -Args @($Path, "/inheritance:r")
Invoke-Icacls -Args @($Path, "/grant:r", "NT AUTHORITY\SYSTEM:(R)")
Invoke-Icacls -Args @($Path, "/grant:r", "BUILTIN\Administrators:(R)")
Invoke-Icacls -Args @($Path, "/grant:r", "NT AUTHORITY\LOCAL SERVICE:(R)")
