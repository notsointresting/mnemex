# Read-only Windows security evidence collector for one controlled
# reproduction of an antivirus block (plan section A0).
#
# Guarantees:
#   * READ-ONLY: never changes antivirus configuration, services, or files.
#   * No uploads, no telemetry; output stays in build\security-evidence.json.
#   * Usernames are redacted from every recorded path.
#   * Access-denied sections are recorded as "unavailable", never escalated.
#
# Usage:  powershell -ExecutionPolicy Bypass -File tools\collect_windows_security_evidence.ps1

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$outDir = Join-Path $root 'build'
if (-not (Test-Path $outDir)) { New-Item -ItemType Directory -Path $outDir | Out-Null }
$outFile = Join-Path $outDir 'security-evidence.json'

function Redact([string]$text) {
    if ($null -eq $text) { return $null }
    return $text -replace '(?i)\\Users\\[^\\]+', '\Users\<redacted>'
}

$notes = @()

# 1. Registered security products (WMI SecurityCenter2; read-only).
try {
    $products = @(Get-CimInstance -Namespace 'root/SecurityCenter2' -ClassName 'AntiVirusProduct' -ErrorAction Stop |
        ForEach-Object {
            @{
                display_name  = $_.displayName
                product_state = $_.productState
                path          = Redact $_.pathToSignedProductExe
            }
        })
} catch {
    $products = 'unavailable'
    $notes += 'SecurityCenter2 query unavailable (access denied or namespace missing).'
}

# 2. Antivirus-related service state (read-only).
$servicePatterns = @('avast', 'bdservicehost', 'vsserv', 'windefend', 'norton', 'symantec', 'mcafee')
$services = @(Get-Service -ErrorAction SilentlyContinue |
    Where-Object {
        $name = $_.Name.ToLower()
        ($servicePatterns | Where-Object { $name -like "*$_*" }).Count -gt 0
    } |
    ForEach-Object { @{ name = $_.Name; display = $_.DisplayName; status = "$($_.Status)" } })

# 3. Windows Defender detection history (read-only; may be unavailable).
try {
    $detections = @(Get-MpThreatDetection -ErrorAction Stop |
        ForEach-Object {
            @{
                threat_id = $_.ThreatID
                time      = if ($_.InitialDetectionTime) { $_.InitialDetectionTime.ToUniversalTime().ToString('o') } else { $null }
                resources = @($_.Resources | ForEach-Object { Redact $_ })
                action    = $_.CleaningActionID
            }
        })
} catch {
    $detections = 'unavailable'
    $notes += 'Defender detection history unavailable (service stopped or access denied).'
}

# 4. Interpreter and installer identity for the tested environment.
$python = @{ path = 'unavailable'; version = 'unavailable' }
$pip = @{ version = 'unavailable' }
$pyCmd = Get-Command python -ErrorAction SilentlyContinue
if ($pyCmd) {
    $python = @{
        path    = Redact $pyCmd.Source
        version = (& $pyCmd.Source --version 2>&1 | Out-String).Trim()
    }
    $pip = @{
        version = (Redact ((& $pyCmd.Source -m pip --version 2>&1 | Out-String).Trim()))
    }
}

# 5. Manual fields the operator fills in for the reproduction event.
$manual = @{
    command_being_run  = '<fill in: exact command that triggered the block>'
    install_mode       = '<fill in: core | vector | openai | dev | source>'
    mnemex_commit      = '<fill in: git rev-parse HEAD>'
    artifact_sha256    = '<fill in: SHA-256 of the installed artifact>'
    scanner_popup_text = '<fill in: vendor, detection/rule name, action shown>'
}

$report = @{
    collected_at        = (Get-Date).ToUniversalTime().ToString('o')
    security_products   = $products
    services            = $services
    defender_detections = $detections
    python              = $python
    pip                 = $pip
    manual              = $manual
    notes               = @($notes)
}

$report | ConvertTo-Json -Depth 6 | Out-File -FilePath $outFile -Encoding utf8
Write-Output "Evidence written to $outFile"
Write-Output 'Fill in the manual section before sharing; verify no personal paths remain.'
