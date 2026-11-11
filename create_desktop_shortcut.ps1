$ErrorActionPreference = 'Stop'

$projectDirectory = $PSScriptRoot
$launcher = Join-Path $projectDirectory 'start_dashboard.ps1'
$executable = Join-Path $projectDirectory 'release\OllamaDashboard.exe'
$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$desktopDirectory = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktopDirectory 'Ollama Dashboard.lnk'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.Description = 'Start Ollama Dashboard and open it in your browser'

if (Test-Path -LiteralPath $executable) {
    $shortcut.TargetPath = $executable
    $shortcut.Arguments = ''
    $shortcut.WorkingDirectory = Split-Path -Parent $executable
    $shortcut.IconLocation = "$executable,0"
}
elseif (Test-Path -LiteralPath $launcher) {
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $shortcut.WorkingDirectory = $projectDirectory
    if (Test-Path -LiteralPath $python) {
        $shortcut.IconLocation = "$python,0"
    }
}
else {
    throw "Neither executable nor script launcher was found in $projectDirectory."
}

$shortcut.Save()

Write-Output "Created desktop shortcut: $shortcutPath"
