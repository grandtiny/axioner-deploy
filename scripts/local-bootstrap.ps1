<#
.SYNOPSIS
    Bootstrap local Windows SSH config for axioner-deploy.

.DESCRIPTION
    1. Generate ed25519 SSH key in ~/.ssh/ if not present
    2. Append Host alias block to ~/.ssh/config if not configured
    3. Push public key to server's ~/.ssh/authorized_keys (one-time
       password auth) via scripts\_install_pubkey.py
    4. Verify ssh <alias> works without password

    Idempotent: re-running will not break existing setup.

.PARAMETER ServerHost
    Server IP. Default: 38.12.23.241

.PARAMETER ServerPort
    SSH port. Default: 22

.PARAMETER ServerUser
    SSH user. Default: root

.PARAMETER Alias
    SSH config alias. Default: axioner

.PARAMETER BootstrapPassword
    One-time password for initial public-key push. After this script
    succeeds, you can change the server password or disable password
    auth entirely.

.EXAMPLE
    .\local-bootstrap.ps1 -BootstrapPassword 'xxxxxx'
#>

[CmdletBinding()]
param(
    [string]$ServerHost = "38.12.23.241",
    [int]$ServerPort = 22,
    [string]$ServerUser = "root",
    [string]$Alias = "axioner",
    [Parameter(Mandatory = $true)]
    [string]$BootstrapPassword
)

$ErrorActionPreference = "Stop"

$sshDir = Join-Path $env:USERPROFILE ".ssh"
$keyPath = Join-Path $sshDir ($Alias + "_ed25519")
$pubPath = $keyPath + ".pub"
$configPath = Join-Path $sshDir "config"

function Write-Step($msg) { Write-Host "[+] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK: $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "    SKIP: $msg" -ForegroundColor Yellow }

# --- 1. Ensure ~/.ssh exists ---
Write-Step "Ensure $sshDir exists"
if (-not (Test-Path $sshDir)) {
    New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
    # NOTE: deliberately not running icacls here. Windows default ACL
    # for user-home subdirectories is already user-only; tightening with
    # icacls /inheritance:r broke Add-Content on some machines.
    Write-Ok "Created $sshDir"
} else {
    Write-Skip "Already exists"
}

# --- 2. Generate ed25519 key if missing ---
Write-Step "Check SSH key: $keyPath"
if (-not (Test-Path $keyPath)) {
    $comment = "axioner-deploy@" + $env:COMPUTERNAME
    & ssh-keygen -t ed25519 -f $keyPath -N '""' -C $comment | Out-Null
    if ($LASTEXITCODE -ne 0) { throw ("ssh-keygen failed (exit=" + $LASTEXITCODE + ")") }
    Write-Ok "Generated $keyPath"
} else {
    Write-Skip "Key already exists"
}

# --- 3. Append config block if not present ---
Write-Step "Check $configPath for Host $Alias"
$marker = "Host " + $Alias
$configBlock = @"

Host $Alias
    HostName $ServerHost
    Port $ServerPort
    User $ServerUser
    IdentityFile $keyPath
    ServerAliveInterval 60
    StrictHostKeyChecking accept-new
"@

$needAppend = $true
if (Test-Path $configPath) {
    $existing = Get-Content $configPath -Raw -ErrorAction SilentlyContinue
    if ($null -ne $existing -and $existing.Contains($marker)) {
        $needAppend = $false
    }
}

if ($needAppend) {
    # Use .NET writer to avoid PowerShell 5.1's UTF8-with-BOM default
    # (OpenSSH does not accept BOM at the start of ~/.ssh/config).
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    if (Test-Path $configPath) {
        [System.IO.File]::AppendAllText($configPath, $configBlock, $utf8NoBom)
    } else {
        [System.IO.File]::WriteAllText($configPath, $configBlock, $utf8NoBom)
    }
    Write-Ok ("Appended Host " + $Alias + " block")
} else {
    Write-Skip ("Host " + $Alias + " already in config")
}

# --- 4. Push public key to server (paramiko via Python helper) ---
Write-Step "Push public key to ${ServerUser}@${ServerHost}:${ServerPort}"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$helper = Join-Path $scriptDir "_install_pubkey.py"
if (-not (Test-Path $helper)) { throw "Helper not found: $helper" }

$pyArgs = @(
    $helper,
    "--host", $ServerHost,
    "--port", $ServerPort,
    "--user", $ServerUser,
    "--password", $BootstrapPassword,
    "--pubkey-file", $pubPath
)
$pushResult = & python $pyArgs 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host $pushResult -ForegroundColor Red
    throw ("Push pubkey failed (exit=" + $LASTEXITCODE + ")")
}
Write-Ok ("Remote response: " + ($pushResult -join " "))

# --- 5. Test passwordless login ---
Write-Step "Test ssh $Alias whoami"
# Temporarily allow stderr-as-warning (ssh prints "Permanently added to known_hosts"
# on first connect, which $ErrorActionPreference=Stop would otherwise treat as fatal).
$prevPref = $ErrorActionPreference
$ErrorActionPreference = "Continue"
try {
    $testStdout = & ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new $Alias whoami 2>$null
    $sshExit = $LASTEXITCODE
} finally {
    $ErrorActionPreference = $prevPref
}
if ($sshExit -ne 0) {
    throw ("ssh " + $Alias + " key auth failed (exit=" + $sshExit + ")")
}
Write-Ok ("Remote whoami: " + ($testStdout -join " ").Trim())

Write-Host ""
Write-Host ("[OK] Bootstrap complete. Use: ssh " + $Alias) -ForegroundColor Green
Write-Host "     Tip: change server password or disable password auth now." -ForegroundColor Yellow
