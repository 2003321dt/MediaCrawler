param(
    [string]$RunnerPath = "D:\A-Programming\APP\ScraplingEvidence\scripts\run-render-lightweight.ps1",
    [string]$TaskName = "MediaCrawlerLightweightDaily",
    [string]$DailyAt = "08:15"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $RunnerPath)) {
    throw "runner not found: $RunnerPath"
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$RunnerPath`""
$trigger = New-ScheduledTaskTrigger -Daily -At $DailyAt
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries
$referenceTask = Get-ScheduledTask -TaskName "ScraplingEvidenceDaily" -ErrorAction SilentlyContinue
$userId = if ($referenceTask -and $referenceTask.Principal.UserId) {
    $referenceTask.Principal.UserId
} else {
    $env:USERNAME
}
$principal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Controlled daily trigger for the Render MediaCrawler lightweight fallback executor." `
    -Force | Out-Null

Get-ScheduledTask -TaskName $TaskName
