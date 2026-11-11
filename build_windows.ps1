$ErrorActionPreference = 'Stop'

$projectDirectory = $PSScriptRoot
$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at $python. Create .venv and install requirements first."
}

& $python -m pip install -r (Join-Path $projectDirectory 'requirements-build.txt')
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install the Windows build requirements.'
}

$releaseDirectory = Join-Path $projectDirectory 'release'
$buildDirectory = Join-Path $projectDirectory 'build'
$icon = Join-Path $projectDirectory 'assets\ollama-dashboard.ico'
if (-not (Test-Path -LiteralPath $icon)) {
    throw "Windows icon not found at $icon."
}
New-Item -ItemType Directory -Force -Path $releaseDirectory, $buildDirectory | Out-Null

& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name OllamaDashboard `
    --icon $icon `
    --distpath $releaseDirectory `
    --workpath $buildDirectory `
    --specpath $buildDirectory `
    --add-data "$(Join-Path $projectDirectory 'templates');templates" `
    --add-data "$(Join-Path $projectDirectory 'static');static" `
    (Join-Path $projectDirectory 'desktop_app.py')
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller failed to build OllamaDashboard.exe.'
}

& (Join-Path $projectDirectory 'create_desktop_shortcut.ps1')
if ($LASTEXITCODE -ne 0) {
    throw 'The executable was built, but the desktop shortcut could not be updated.'
}

Write-Output "Built executable: $(Join-Path $releaseDirectory 'OllamaDashboard.exe')"
