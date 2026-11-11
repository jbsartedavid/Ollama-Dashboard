$ErrorActionPreference = 'Stop'

$projectDirectory = $PSScriptRoot
$launcher = Join-Path $projectDirectory 'start_dashboard.ps1'
$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDirectory 'Ollama Dashboard.lnk'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

if (-not (Test-Path -LiteralPath $launcher)) {
    throw "Dashboard launcher not found: $launcher"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
$shortcut.WorkingDirectory = $projectDirectory
$shortcut.Description = 'Start Ollama Dashboard and open it in your browser'
if (Test-Path -LiteralPath $python) {
    $shortcut.IconLocation = "$python,0"
}
$shortcut.Save()

Write-Output "Created desktop shortcut: $shortcutPath"
