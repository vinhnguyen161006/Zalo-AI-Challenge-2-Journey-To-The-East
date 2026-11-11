param(
    [string]$Config = "configs/qwen35-evaluation.yaml",
    [string]$Work = "outputs/qwen35-finalization",
    [ValidateRange(1, 1000)][int]$QuestionsPerBusiness = 20
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$python = Join-Path (Get-Location) ".venv/Scripts/python.exe"
$configPath = (Resolve-Path -LiteralPath $Config).Path
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$workPath = (Resolve-Path -LiteralPath $Work).Path
$planPath = Join-Path $workPath "question-plan"
$calibrationOutput = Join-Path $workPath "calibration.jsonl"
$artifactPath = Join-Path $workPath "calibration-artifact.json"
$finalConfigPath = Join-Path $workPath "final-config.yaml"
$statusPath = Join-Path $workPath "status.json"
$startedAt = [DateTime]::UtcNow.ToString("o")
$phase = "prepare"

function Write-Status([string]$State, [string]$Message = "") {
    $status = @{
        state = $State; phase = $script:phase; message = $Message
        started_at = $script:startedAt; updated_at = [DateTime]::UtcNow.ToString("o")
        process_id = $PID; config = $configPath; work = $workPath
    } | ConvertTo-Json
    [IO.File]::WriteAllText("$statusPath.tmp", $status, [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath "$statusPath.tmp" -Destination $statusPath -Force
}

function Run-Python([string[]]$RunArguments) {
    & $python @RunArguments
    if ($LASTEXITCODE -ne 0) { throw "Python phase failed with exit code $LASTEXITCODE" }
}

try {
    Write-Status "running"
    $identityJson = & $python -c "import json,sys; from pathlib import Path; from datamate.config import load_config; from datamate.data.ingest import sha256_file; c=load_config(Path(sys.argv[1])); print(json.dumps({'seed':c.seed,'questions_sha256':sha256_file(c.questions)}))" $configPath
    if ($LASTEXITCODE -ne 0) { throw "Cannot identify the evaluation configuration" }
    $configIdentity = $identityJson | ConvertFrom-Json
    if (-not (Test-Path -LiteralPath (Join-Path $planPath "manifest.json"))) {
        Run-Python @("scripts/prepare_evaluation.py", "--config", $configPath, "--destination", $planPath, "--per-business", "$QuestionsPerBusiness")
    }
    else {
        $manifest = Get-Content -LiteralPath (Join-Path $planPath "manifest.json") -Raw | ConvertFrom-Json
        if ($manifest.per_business -ne $QuestionsPerBusiness) {
            throw "Existing question plan uses a different QuestionsPerBusiness. Use its original value or a new Work directory."
        }
        if ($manifest.seed -ne $configIdentity.seed -or $manifest.questions_sha256 -ne $configIdentity.questions_sha256) {
            throw "Existing question plan uses a different seed or question dataset. Use a new Work directory."
        }
    }
    $phase = "calibration_inference"
    Write-Status "running"
    Run-Python @("-m", "datamate", "evaluate", "--config", $configPath, "--questions", (Join-Path $planPath "calibration.jsonl"), "--labels", "data/train/answers.jsonl", "--output", $calibrationOutput, "--resume")
    $phase = "fit_calibration"
    Write-Status "running"
    if (-not (Test-Path -LiteralPath $artifactPath)) {
        Run-Python @("-m", "datamate", "calibrate", "--config", $configPath, "--labels", "data/train/answers.jsonl", "--traces", (Join-Path $workPath "calibration.logs"), "--output", $artifactPath)
    }
    $artifact = Get-Content -LiteralPath $artifactPath -Raw | ConvertFrom-Json
    $usable = @($artifact.buckets.PSObject.Properties | Where-Object { $_.Value.sufficient -and $_.Value.lower_bound -gt 0.5 })
    if ($usable.Count -eq 0) {
        throw "No calibration bucket has enough businesses/examples with lower bound > 0.5. Final policy is not ready; inspect calibration traces before collecting more train examples."
    }
    $finalConfig = @{
        extends = $configPath
        decision_policy = "calibrated"
        calibration_artifact = $artifactPath
        output_root = (Join-Path $workPath "final-runtime")
    } | ConvertTo-Json
    [IO.File]::WriteAllText($finalConfigPath, $finalConfig, [Text.UTF8Encoding]::new($false))
    $phase = "holdout_evaluation"
    Write-Status "running"
    Run-Python @("-m", "datamate", "evaluate", "--config", $finalConfigPath, "--questions", (Join-Path $planPath "holdout.jsonl"), "--labels", "data/train/answers.jsonl", "--output", (Join-Path $workPath "holdout.jsonl"), "--resume")
    $phase = "complete"
    Write-Status "complete"
    Write-Output "Finished calibrated model evaluation. Config: $finalConfigPath"
}
catch {
    Write-Status "failed" $_.Exception.Message
    throw
}
