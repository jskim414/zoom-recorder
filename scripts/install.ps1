param([string]$Python = "python", [switch]$Dev, [switch]$EnableStartup, [switch]$WithTranscription, [switch]$WithGpu)
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
& $Python -c "import sys; assert sys.version_info >= (3,11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 이상을 설치하고 다시 실행하세요.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '가상환경 생성 실패' }
}
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$requirements = if ($Dev) { 'requirements-dev.txt' } elseif ($WithTranscription) { 'requirements.txt' } else { 'requirements-core.txt' }
& $pythonPath -m pip install --only-binary=:all: -r $requirements
if ($LASTEXITCODE -ne 0) { throw '패키지 설치 실패. INSTALL_WINDOWS.md의 호환성 안내를 확인하세요.' }
if ($WithGpu) {
    & $pythonPath -m pip install --only-binary=:all: -r requirements-gpu.txt
    if ($LASTEXITCODE -ne 0) { throw 'GPU runtime installation failed. See INSTALL_WINDOWS.md.' }
}
& $pythonPath -c "from app.paths import default_config; from shutil import copyfile; p=default_config(); p.parent.mkdir(parents=True, exist_ok=True); copyfile('config/settings.example.yaml', p) if not p.exists() else None"
if ($LASTEXITCODE -ne 0) { throw 'Settings initialization failed' }
if ($EnableStartup) {
    $startupPath = [Environment]::GetFolderPath('Startup')
    $shortcutPath = Join-Path $startupPath 'Zoom Recorder Agent.lnk'
    $shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
    $shortcut.TargetPath = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
    $shortcut.Arguments = '-m app.main --background'
    $shortcut.WorkingDirectory = $projectRoot
    $shortcut.Description = '동의 확인 후에만 녹음하는 로컬 Zoom Recorder'
    $shortcut.Save()
}
& $pythonPath -m scripts.create_shortcut
if ($LASTEXITCODE -ne 0) { throw '실행 바로가기 생성 실패' }
Write-Output '설치 완료. 프로젝트 폴더의 Zoom Recorder 바로가기로 실행하세요. 최초 오디오 검증은 scripts\run.ps1 -Poc를 사용하세요.'
