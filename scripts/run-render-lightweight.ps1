param(
    [string]$ExecutorUrl = "https://xiaod-mediacrawler-executor.onrender.com",
    [int]$PollSeconds = 15,
    [int]$MaxWaitSeconds = 600,
    [string]$LogDirectory = "D:\A-Programming\APP\ScraplingEvidence\logs\mediacrawler-lightweight"
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$context = "xiaod-mediacrawler-run:v1`0"
$rawUrl = "https://raw.githubusercontent.com/2003321dt/MediaCrawler/main/outputs/mediacrawler/latest-hotspots.json"
$startedAt = [DateTimeOffset]::UtcNow
$result = [ordered]@{
    status = "starting"
    started_at = $startedAt.ToString("o")
    finished_at = $null
    trigger_status = $null
    executor_status = $null
    output_status = $null
    output_finished_at = $null
    items = 0
    errors = 0
    message = $null
}

function Save-Result {
    param([System.Collections.IDictionary]$Value)
    New-Item -ItemType Directory -Path $LogDirectory -Force | Out-Null
    $Value.finished_at = [DateTimeOffset]::UtcNow.ToString("o")
    $json = $Value | ConvertTo-Json -Depth 12
    $stamp = [DateTimeOffset]::Now.ToString("yyyyMMdd-HHmmss")
    $historyPath = Join-Path $LogDirectory "$stamp.json"
    $latestPath = Join-Path $LogDirectory "latest.json"
    [IO.File]::WriteAllText($historyPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($latestPath, $json + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
}

function Get-DerivedToken {
    $githubToken = [Environment]::GetEnvironmentVariable("GITHUB_TOKEN", "User")
    if ([string]::IsNullOrWhiteSpace($githubToken)) {
        throw "GITHUB_TOKEN is not configured for the scheduled-task user"
    }
    $bytes = [Text.Encoding]::UTF8.GetBytes($context + $githubToken)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash($bytes)
        return -join ($hash | ForEach-Object { $_.ToString("x2") })
    }
    finally {
        $sha.Dispose()
        [Array]::Clear($bytes, 0, $bytes.Length)
        $githubToken = $null
    }
}

try {
    $derivedToken = Get-DerivedToken
    $headers = @{ "X-Run-Token" = $derivedToken }
    $trigger = Invoke-RestMethod -Method Post -Uri "$ExecutorUrl/run" -Headers $headers -TimeoutSec 90
    $result.trigger_status = [string]$trigger.status

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($MaxWaitSeconds)
    do {
        Start-Sleep -Seconds $PollSeconds
        $status = Invoke-RestMethod -Uri "$ExecutorUrl/status?ts=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" -TimeoutSec 90
        $result.executor_status = [string]$status.executor.status
        if ($status.latest) {
            $result.output_status = [string]$status.latest.status
            $result.output_finished_at = [string]$status.latest.finished_at
            $result.items = @($status.latest.items).Count
            $result.errors = @($status.latest.errors).Count
        }
        $running = $result.executor_status -in @("running", "never_run")
    } while ($running -and [DateTimeOffset]::UtcNow -lt $deadline)

    if ($running) {
        throw "executor did not finish within $MaxWaitSeconds seconds"
    }

    $raw = Invoke-RestMethod -Uri "$rawUrl?ts=$([DateTimeOffset]::UtcNow.ToUnixTimeSeconds())" -TimeoutSec 90
    $result.output_status = [string]$raw.status
    $result.output_finished_at = [string]$raw.finished_at
    $result.items = @($raw.items).Count
    $result.errors = @($raw.errors).Count

    $outputTime = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($result.output_finished_at, [ref]$outputTime) -or $outputTime -lt $startedAt.AddMinutes(-1)) {
        throw "GitHub output did not refresh after the controlled trigger"
    }
    $result.status = if ($result.executor_status -in @("success", "restored_from_output")) { "success" } else { "failed" }
    Save-Result $result
    if ($result.status -ne "success") { exit 1 }
}
catch {
    $result.status = "failed"
    $result.message = $_.Exception.Message
    Save-Result $result
    exit 1
}
finally {
    if ($derivedToken) { $derivedToken = $null }
}
