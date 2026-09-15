# N034: Grant LocalService Modify on product-owned runtime dirs under the install tree.
# Uses icacls.exe (not Get-Acl/Set-Acl) so GitHub Actions pwsh runners without a
# loadable Microsoft.PowerShell.Security module still harden ACLs.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File harden_service_dirs.ps1 -AppDir <dir>
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $AppDir)) {
    throw "AppDir does not exist: $AppDir"
}

function Invoke-Icacls {
    param([Parameter(Mandatory = $true)][string[]]$Args)
    & icacls.exe @Args
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed ($LASTEXITCODE): icacls $($Args -join ' ')"
    }
}

$dirs = @("logs", "tmp", "sandbox") | ForEach-Object { Join-Path $AppDir $_ }
foreach ($dir in $dirs) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    # Disable inheritance and wipe inherited ACEs, then grant explicit principals.
    Invoke-Icacls -Args @($dir, "/inheritance:r")
    Invoke-Icacls -Args @($dir, "/grant:r", "NT AUTHORITY\SYSTEM:(OI)(CI)(F)")
    Invoke-Icacls -Args @($dir, "/grant:r", "BUILTIN\Administrators:(OI)(CI)(F)")
    # LOCAL SERVICE needs Modify so the service can write logs/tmp/sandbox under Program Files.
    Invoke-Icacls -Args @($dir, "/grant:r", "NT AUTHORITY\LOCAL SERVICE:(OI)(CI)(M)")
}
