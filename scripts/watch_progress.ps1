param(
    [string]$Work = "outputs/docker-finalization-cpu12",
    [switch]$Watch,
    [ValidateRange(2, 3600)][int]$IntervalSeconds = 10,
    [switch]$Logs,
    [switch]$NoDocker
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$workPath = (Resolve-Path -LiteralPath $Work).Path

function Read-JsonFile([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) }
        catch { return $null } # An atomic status replacement may be in progress.
    }
    return $null
}
function Count-Records([string]$Path) {
    $count = 0
    if (Test-Path -LiteralPath $Path) {
        foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
            if (-not [string]::IsNullOrWhiteSpace($line)) {
                try {
                    $record = $line | ConvertFrom-Json
                    if ($null -ne $record.id -and $null -ne $record.type) { $count++ }
                }
                catch { } # Do not count a partially written final line.
            }
        }
    }
    return $count
}
function Docker-Text([string[]]$Arguments) {
    $stdout = [IO.Path]::GetTempFileName()
    $stderr = [IO.Path]::GetTempFileName()
    try {
        $capture = Start-Process -FilePath docker.exe -WindowStyle Hidden -Wait -PassThru `
            -ArgumentList $Arguments -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        if ($capture.ExitCode -ne 0) { throw "Docker unavailable (exit $($capture.ExitCode))" }
        $lines = @(Get-Content -LiteralPath $stdout -Encoding UTF8)
        $lines += @(Get-Content -LiteralPath $stderr -Encoding UTF8)
        return $lines
    }
    finally { Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue }
}

do {
    if ($Watch) { Clear-Host }
    $outer = Read-JsonFile (Join-Path $workPath "status.json")
    $inner = Read-JsonFile (Join-Path $workPath "training/status.json")
    $manifest = Read-JsonFile (Join-Path $workPath "training/question-plan/manifest.json")
    $request = Read-JsonFile (Join-Path $workPath "request.json")
    $phase = $outer.phase
    if ($phase -eq "linux_calibration_and_holdout" -and $inner) { $phase = $inner.phase }
    $phaseNames = @{
        freeze_image = "Freeze image"; prepare_only = "Prepare only"; prepare = "Prepare partitions"
        calibration_inference = "Calibration inference"; fit_calibration = "Fit calibration policy"
        holdout_evaluation = "Holdout evaluation"; build_calibrated_image = "Build calibrated image"
        public_inference = "Public inference"; validate_public_file = "Validate public JSONL"; complete = "Complete"
    }
    $description = if ($phase -and $phaseNames.ContainsKey($phase)) { $phaseNames[$phase] } else { $phase }
    Write-Output "DataMate progress -- $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
    Write-Output "Work: $workPath"
    Write-Output "State: $($outer.state) | Stage: $description ($phase)"
    if ($outer.message) { Write-Output "Controller: $($outer.message)" }
    if ($inner.state -eq "failed") { Write-Output "Training failed: $($inner.message)" }
    $calibration = Count-Records (Join-Path $workPath "training/calibration.jsonl")
    $holdout = Count-Records (Join-Path $workPath "training/holdout.jsonl")
    $public = Count-Records (Join-Path $workPath "public/submission.jsonl")
    $publicTotal = "?"
    if ($request.public) {
        $questions = Join-Path $request.public "questions.jsonl"
        if (Test-Path -LiteralPath $questions) {
            $publicTotal = @(Get-Content -LiteralPath $questions | Where-Object { $_.Trim() }).Count
        }
    }
    Write-Output "Saved questions (includes abstain; not accuracy):"
    Write-Output "  Calibration: $calibration / $($manifest.counts.calibration)"
    Write-Output "  Holdout:     $holdout / $($manifest.counts.holdout)"
    Write-Output "  Public:      $public / $publicTotal"
    if (-not $NoDocker) {
        try {
            $hasher = [Security.Cryptography.SHA256]::Create()
            try { $suffix = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($workPath)))).Replace("-", "").Substring(0, 10).ToLowerInvariant() }
            finally { $hasher.Dispose() }
            $name = if ($outer.phase -in @("public_inference", "validate_public_file", "complete")) { "datamate-public-$suffix" } else { "datamate-cal-$suffix" }
            if ($outer.phase -eq "prepare_only") { $name += "-prepare" }
            $info = ((Docker-Text @("inspect", $name)) -join "`n" | ConvertFrom-Json)[0]
            if ($info.Config.Labels.'org.datamate.work' -ne $workPath) { throw "Container ownership differs" }
            Write-Output "Container: $name | $($info.State.Status) | exit=$($info.State.ExitCode)"
            $tail = @(Docker-Text @("logs", "--tail", "60", $name))
            $current = $null
            $modelRole = $null
            foreach ($line in $tail) {
                try { $event = $line | ConvertFrom-Json } catch { continue }
                if ($event.event -eq "query_started") { $current = $event.question_id; $modelRole = $null }
                if ($event.event -eq "model_request") { $modelRole = $event.role }
                if ($event.event -eq "query_completed") { $current = $null; $modelRole = $null }
            }
            if ($current) { Write-Output "Latest active question in log: $current | model role: $modelRole" }
            if ($info.State.Status -ne "running" -and $outer.state -eq "running") {
                Write-Output "Container is not running; saved controller status may be stale. Check controller or resume command."
            }
            if ($Logs) { Write-Output "--- Container log tail ---"; $tail | Write-Output }
        }
        catch { Write-Output "Live Docker check unavailable: $($_.Exception.Message). File counts above remain available." }
    }
    Write-Output "Counts are completed records only. Stage timestamps are not a heartbeat. No ETA is estimated."
    if ($Watch) {
        Write-Output "Refreshing every $IntervalSeconds seconds. Ctrl+C closes this viewer; inference keeps running."
        Start-Sleep -Seconds $IntervalSeconds
    }
} while ($Watch)
