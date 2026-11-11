param(
    [string]$Split = "data/public_test",
    [string]$Result = "outputs/submission",
    [string]$Config = "configs/qwen35-evaluation.yaml",
    [int]$Limit = 0
)
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$runArguments = @("-m", "datamate", "submit", "--config", $Config, "--split", $Split, "--result", $Result, "--resume")
if ($Limit -gt 0) { $runArguments += @("--limit", "$Limit") }
& ".\.venv\Scripts\python.exe" @runArguments
exit $LASTEXITCODE
