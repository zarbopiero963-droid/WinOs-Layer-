# N034: Grant LocalService Modify on product-owned runtime dirs under the install tree.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File harden_service_dirs.ps1 -AppDir <dir>
param(
    [Parameter(Mandatory = $true)]
    [string]$AppDir
)

$ErrorActionPreference = "Stop"

if (-not (Test-Path -LiteralPath $AppDir)) {
    throw "AppDir does not exist: $AppDir"
}

$dirs = @("logs", "tmp", "sandbox") | ForEach-Object { Join-Path $AppDir $_ }
foreach ($dir in $dirs) {
    if (-not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $acl = Get-Acl -LiteralPath $dir
    $acl.SetAccessRuleProtection($true, $false)
    foreach ($rule in @($acl.Access)) {
        [void]$acl.RemoveAccessRule($rule)
    }
    $grants = @(
        @{ Principal = "NT AUTHORITY\SYSTEM"; Rights = "FullControl" },
        @{ Principal = "BUILTIN\Administrators"; Rights = "FullControl" },
        @{ Principal = "NT AUTHORITY\LOCAL SERVICE"; Rights = "Modify,Synchronize" }
    )
    foreach ($grant in $grants) {
        $access = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $grant.Principal,
            $grant.Rights,
            "ContainerInherit,ObjectInherit",
            "None",
            "Allow"
        )
        $acl.AddAccessRule($access)
    }
    Set-Acl -LiteralPath $dir -AclObject $acl
}
