$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$projectDirectory = $PSScriptRoot
$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectDirectory 'logs'
$stdoutLog = Join-Path $logDirectory 'dashboard.stdout.log'
$stderrLog = Join-Path $logDirectory 'dashboard.stderr.log'
$dashboardUrl = 'http://127.0.0.1:5000/'

function Test-Dashboard {
    try {
        $response = Invoke-WebRequest -Uri $dashboardUrl -UseBasicParsing -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    [System.Windows.Forms.MessageBox]::Show(
        "The dashboard Python environment was not found.`n`nRun the setup commands in README.md, then try again.",
        'Ollama Dashboard',
        'OK',
        'Error'
    ) | Out-Null
    exit 1
}

if (-not (Test-Dashboard)) {
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Start-Process `
        -FilePath $python `
        -ArgumentList 'app.py' `
        -WorkingDirectory $projectDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $stdoutLog `
        -RedirectStandardError $stderrLog | Out-Null

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Seconds 1
        if (Test-Dashboard) {
            $ready = $true
            break
        }
    }

    if (-not $ready) {
        $details = ''
        if (Test-Path -LiteralPath $stderrLog) {
            $details = (Get-Content -LiteralPath $stderrLog -Tail 8 -ErrorAction SilentlyContinue) -join "`n"
        }
        [System.Windows.Forms.MessageBox]::Show(
            "The dashboard did not start. Check that Ollama Dashboard dependencies are installed.$(if ($details) { "`n`n$details" })",
            'Ollama Dashboard',
            'OK',
            'Error'
        ) | Out-Null
        exit 1
    }
}

Start-Process $dashboardUrl
