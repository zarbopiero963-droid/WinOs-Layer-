# N034: CSPRNG api_key.txt + restrictive DACL (SYSTEM / Administrators / LocalService).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File write_secure_api_key.ps1 -Path <file>
param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

$ErrorActionPreference = "Stop"

$dir = Split-Path -Parent -Path $Path
if (-not [string]::IsNullOrWhiteSpace($dir) -and -not (Test-Path -LiteralPath $dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
}

$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$hex = -join ($bytes | ForEach-Object { $_.ToString("x2") })
[System.IO.File]::WriteAllText($Path, $hex + [Environment]::NewLine)

$acl = Get-Acl -LiteralPath $Path
$acl.SetAccessRuleProtection($true, $false)
foreach ($rule in @($acl.Access)) {
    [void]$acl.RemoveAccessRule($rule)
}
$principals = @(
    "NT AUTHORITY\SYSTEM",
    "BUILTIN\Administrators",
    "NT AUTHORITY\LOCAL SERVICE"
)
foreach ($principal in $principals) {
    $access = New-Object System.Security.AccessControl.FileSystemAccessRule(
        $principal,
        "Read,ReadAndExecute",
        "Allow"
    )
    $acl.AddAccessRule($access)
}
Set-Acl -LiteralPath $Path -AclObject $acl
