param(
    [string]$Python = "",
    [string]$ScriptPath = "$PSScriptRoot\hermes_au_bag_alert.py",
    [string]$TaskName = "Hermes AU Bag Alert",
    [int]$EveryMinutes = 10
)

$pythonCandidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe"
)

if (-not $Python) {
    foreach ($candidate in $pythonCandidates) {
        if (Test-Path -LiteralPath $candidate) {
            $Python = $candidate
            break
        }
    }
}

if (-not $Python) {
    $command = Get-Command python -ErrorAction SilentlyContinue
    if ($command) {
        $Python = $command.Source
    }
}

if (-not $Python) {
    throw "Python was not found. Install Python 3.12+ first, or pass -Python C:\Path\To\python.exe"
}

$resolvedScript = Resolve-Path -LiteralPath $ScriptPath
$action = New-ScheduledTaskAction -Execute $Python -Argument "`"$resolvedScript`""
$trigger = New-ScheduledTaskTrigger `
    -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $EveryMinutes) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Checks Hermes AU women's bags and emails new product alerts." `
    -Force | Out-Null

Write-Host "Scheduled task installed: $TaskName"
Write-Host "Runs every $EveryMinutes minute(s)."
Write-Host "Script: $resolvedScript"
Write-Host "Python: $Python"
