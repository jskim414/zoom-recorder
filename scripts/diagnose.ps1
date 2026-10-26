param([string]$OutputFile = "")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw '먼저 scripts\install.ps1을 실행하세요.' }
$env:PYTHONIOENCODING = 'utf-8'
if (-not $OutputFile) { $OutputFile = & $pythonPath -c "from app.paths import logs_root; print(logs_root() / 'diagnostics.local.json')" }
$outputDirectory = Split-Path -Parent ([IO.Path]::GetFullPath($OutputFile))
New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null
& $pythonPath -m app.diagnostics --output $OutputFile
if ($LASTEXITCODE -ne 0) { throw '진단 실행 실패' }
