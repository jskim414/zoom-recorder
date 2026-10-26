param([ValidateSet('lite', 'local')][string]$Flavor = 'lite', [string]$Python = '.\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:ZOOM_BUILD_FLAVOR = $Flavor
$workspaceRoot = Split-Path -Parent $projectRoot
$distributionRoot = Join-Path $projectRoot 'dist'
$buildRoot = Join-Path $projectRoot 'logs\build'
if (Test-Path -LiteralPath (Join-Path $workspaceRoot '.zoom-workspace')) {
    $distributionRoot = Join-Path $workspaceRoot 'releases'
    $buildRoot = Join-Path $workspaceRoot 'logs\build'
}
& $Python -m PyInstaller --noconfirm --distpath $distributionRoot --workpath $buildRoot packaging/zoom-recorder.spec
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed' }
$name = if ($Flavor -eq 'local') { 'ZoomRecorder-Local' } else { 'ZoomRecorder-Lite' }
$destination = Join-Path $distributionRoot $name
# Explicit allowlist excludes private config, recordings, models, keys and diagnostics.
Copy-Item -LiteralPath 'browser-extension' -Destination $destination -Recurse -Force
foreach ($document in @('README.md', 'VERSION')) {
    Copy-Item -LiteralPath $document -Destination $destination -Force
}
Copy-Item -LiteralPath 'packaging/START_HERE.html' -Destination $destination -Force
if (Test-Path -LiteralPath 'LICENSE') {
    Copy-Item -LiteralPath 'LICENSE' -Destination $destination -Force
}
& $Python -m scripts.package_licenses $destination
if ($LASTEXITCODE -ne 0) { throw 'License inventory failed' }
$archive = Join-Path $distributionRoot "$name-windows-x64.zip"
Compress-Archive -LiteralPath $destination -DestinationPath $archive -Force
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLower()
Set-Content -LiteralPath "$archive.sha256" -Value "$hash  $([IO.Path]::GetFileName($archive))" -Encoding ascii
Write-Output "Built $archive"
