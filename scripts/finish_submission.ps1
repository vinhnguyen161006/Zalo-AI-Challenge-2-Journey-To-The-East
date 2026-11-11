param(
    [string]$Image = "datamate-local:qwen35",
    [string]$Work = "outputs/docker-finalization",
    [string]$TrainSplit = "data/train",
    [string]$PublicSplit = "data/public_test",
    [string]$FinalImage = "datamate-local:calibrated",
    [ValidateRange(1, 1000)][int]$QuestionsPerBusiness = 20,
    [ValidateRange(1, 256)][int]$Cpus = 6,
    [string]$Memory = "12g",
    [switch]$UseGpu,
    [switch]$PrepareOnly
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
$trainPath = (Resolve-Path -LiteralPath $TrainSplit).Path
$publicPath = (Resolve-Path -LiteralPath $PublicSplit).Path
New-Item -ItemType Directory -Force -Path $Work | Out-Null
$workPath = (Resolve-Path -LiteralPath $Work).Path
$trainResult = Join-Path $workPath "training"
$publicResult = Join-Path $workPath "public"
New-Item -ItemType Directory -Force -Path $trainResult, $publicResult | Out-Null
$phase = "freeze_image"
$started = [DateTime]::UtcNow.ToString("o")

function Write-Json([string]$Path, $Value) {
    [IO.File]::WriteAllText("$Path.tmp", ($Value | ConvertTo-Json -Depth 10), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath "$Path.tmp" -Destination $Path -Force
}
function Write-Status([string]$State, [string]$Message = "") {
    Write-Json (Join-Path $workPath "status.json") @{
        state = $State; phase = $script:phase; message = $Message; process_id = $PID
        started_at = $script:started; updated_at = [DateTime]::UtcNow.ToString("o")
    }
}
function Run-Docker([string[]]$DockerArguments) {
    & docker @DockerArguments
    if ($LASTEXITCODE -ne 0) { throw "Docker command failed with exit code $LASTEXITCODE" }
}
function Run-Container([string]$Name, [string]$ContainerImage, [string[]]$RunArguments, [string[]]$CommandArguments = @()) {
    $existing = & docker container ls -a --filter "name=^/$Name`$" --format '{{.Names}}'
    if ($LASTEXITCODE -ne 0) { throw "Cannot inspect container list" }
    if ($existing) {
        $inspectText = & docker inspect $Name
        if ($LASTEXITCODE -ne 0) { throw "Cannot inspect existing container" }
        $info = @($inspectText | ConvertFrom-Json)[0]
        $expectedId = & docker image inspect --format '{{.Id}}' $ContainerImage
        if ($LASTEXITCODE -ne 0 -or $info.Config.Labels.'org.datamate.work' -ne $workPath -or $info.Image -ne $expectedId) {
            throw "Container ownership or image differs; use a new Work directory"
        }
        if ($CommandArguments.Count -gt 0 -and (@($info.Config.Cmd) -join "`n") -ne ($CommandArguments -join "`n")) {
            throw "Existing container command differs; use a new Work directory"
        }
        if ($info.State.Status -eq "exited" -and $info.State.ExitCode -eq 0) { return }
        if (-not $info.State.Running) { Run-Docker @("start", $Name) }
    }
    else {
        $deviceArguments = @()
        if ($UseGpu) { $deviceArguments = @("--gpus", "all") }
        Run-Docker (@("run", "-d", "--name", $Name, "--label", "org.datamate.work=$workPath",
            "--network", "none", "--cpus", "$Cpus", "--memory", $Memory,
            "--env", "OLLAMA_MAX_LOADED_MODELS=1", "--env", "OLLAMA_NUM_PARALLEL=1") + $deviceArguments + $RunArguments + @($ContainerImage) + $CommandArguments)
    }
    $containerExit = & docker wait $Name
    if ($LASTEXITCODE -ne 0) { throw "Cannot wait for container $Name" }
    # Windows PowerShell treats redirected native stderr as ErrorRecord objects.
    # Docker uses stderr for normal application logs, so capture outside that path.
    $capture = Start-Process -FilePath docker.exe -WindowStyle Hidden -Wait -PassThru `
        -ArgumentList @("logs", $Name) `
        -RedirectStandardOutput (Join-Path $workPath "$Name.stdout.log") `
        -RedirectStandardError (Join-Path $workPath "$Name.stderr.log")
    if ($capture.ExitCode -ne 0) { throw "Cannot capture logs for container $Name" }
    if ([int]$containerExit -ne 0) { throw "Container $Name failed (exit $containerExit); inspect its logs and training/status.json" }
}

try {
    Write-Status "running"
    $requestPath = Join-Path $workPath "request.json"
    $request = @{train = $trainPath; public = $publicPath; per_business = $QuestionsPerBusiness; cpus = $Cpus; memory = $Memory; gpu = [bool]$UseGpu}
    if (Test-Path -LiteralPath $requestPath) {
        $previous = Get-Content -LiteralPath $requestPath -Raw | ConvertFrom-Json
        # Older CPU-only work directories predate the GPU switch.
        if (-not ($previous.PSObject.Properties.Name -contains "gpu")) { $previous | Add-Member -NotePropertyName gpu -NotePropertyValue $false }
        foreach ($key in $request.Keys) {
            if ($previous.$key -ne $request[$key]) { throw "Work directory belongs to different inputs/settings; use a new Work directory" }
        }
    }
    else { Write-Json $requestPath $request }
    $imageRecord = Join-Path $workPath "image.json"
    if (Test-Path -LiteralPath $imageRecord) {
        $frozen = Get-Content -LiteralPath $imageRecord -Raw | ConvertFrom-Json
        $actualId = & docker image inspect --format '{{.Id}}' $frozen.tag
        if ($LASTEXITCODE -ne 0 -or $actualId -ne $frozen.id) { throw "Frozen image changed or disappeared" }
    }
    else {
        $imageId = & docker image inspect --format '{{.Id}}' $Image
        if ($LASTEXITCODE -ne 0) { throw "Build the offline runtime image first" }
        $frozen = @{id = $imageId; tag = "datamate-local:frozen-$($imageId.Substring(7, 12))"}
        Run-Docker @("tag", $imageId, $frozen.tag)
        Write-Json $imageRecord $frozen
    }
    $hasher = [Security.Cryptography.SHA256]::Create()
    $suffix = ([BitConverter]::ToString($hasher.ComputeHash([Text.Encoding]::UTF8.GetBytes($workPath)))).Replace("-", "").Substring(0, 10).ToLowerInvariant()
    $hasher.Dispose()
    $phase = if ($PrepareOnly) { "prepare_only" } else { "linux_calibration_and_holdout" }
    Write-Status "running"
    $trainingName = "datamate-cal-$suffix" + $(if ($PrepareOnly) { "-prepare" } else { "" })
    $trainingArguments = @("--per-business", "$QuestionsPerBusiness")
    if ($PrepareOnly) { $trainingArguments += "--prepare-only" }
    Run-Container $trainingName $frozen.tag @("--entrypoint", "/app/train.sh",
        "--env", "DATAMATE_CONFIG=/app/configs/qwen35-docker.yaml",
        "--mount", "type=bind,source=$trainPath,target=/train,readonly",
        "--mount", "type=bind,source=$trainResult,target=/result") $trainingArguments
    if ($PrepareOnly) { Write-Status "prepared" "No model inference or label contents read"; exit 0 }
    $phase = "build_calibrated_image"
    Write-Status "running"
    Run-Docker @("build", "-f", "Dockerfile.calibrated", "--build-context", "calibration_store=$trainResult/release",
        "--build-arg", "RUNTIME_IMAGE=$($frozen.tag)", "-t", $FinalImage, ".")
    $phase = "public_inference"
    Write-Status "running"
    Run-Container "datamate-public-$suffix" $FinalImage @(
        "--mount", "type=bind,source=$publicPath,target=/data,readonly",
        "--mount", "type=bind,source=$publicResult,target=/result")
    $phase = "validate_public_file"
    Write-Status "running"
    $validationConfig = Join-Path $workPath "validation-config.yaml"
    Write-Json $validationConfig @{
        extends = (Resolve-Path -LiteralPath "configs/public.yaml").Path
        data_root = (Join-Path $publicPath "data"); questions = (Join-Path $publicPath "questions.jsonl")
        rulebook = (Join-Path $publicPath "rulebook.md"); calendar = (Join-Path $publicPath "calendar.csv")
    }
    & ".\.venv\Scripts\python.exe" -m datamate validate --config $validationConfig --predictions (Join-Path $publicResult "submission.jsonl")
    if ($LASTEXITCODE -ne 0) { throw "Local public-file validation failed" }
    $phase = "complete"
    Write-Status "complete" "Local workflow complete; official evaluator and target-GPU throughput remain unverified"
    Write-Output "Submission: $(Join-Path $publicResult 'submission.jsonl')"
}
catch {
    Write-Status "failed" $_.Exception.Message
    throw
}
