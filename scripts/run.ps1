param([switch]$Poc, [switch]$NoDetection)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '먼저 scripts\install.ps1을 실행하세요.' }
$appArgs = @('-m', 'app.main')
if ($Poc) { $appArgs += '--poc' }
if ($NoDetection) { $appArgs += '--no-detection' }
& $pythonPath @appArgs
if ($LASTEXITCODE -ne 0) { throw "앱 종료 코드: $LASTEXITCODE" }
