$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms

$projectDirectory = $PSScriptRoot
$python = Join-Path $projectDirectory '.venv\Scripts\python.exe'
$logDirectory = Join-Path $projectDirectory 'logs'
$stdoutLog = Join-Path $logDirectory 'dashboard.stdout.log'
$stderrLog = Join-Path $logDirectory 'dashboard.stderr.log'
$dashboardPort = 5111
if ($env:OLLAMA_DASHBOARD_PORT) {
    $dashboardPort = [int]$env:OLLAMA_DASHBOARD_PORT
}
else {
    try {
        $userPort = [Environment]::GetEnvironmentVariable(
            'OLLAMA_DASHBOARD_PORT',
            [EnvironmentVariableTarget]::User
        )
        if ($userPort) {
            $dashboardPort = [int]$userPort
        }
    }
    catch {
        throw "Could not read the configured dashboard port: $($_.Exception.Message)"
    }
}

if ($dashboardPort -lt 1 -or $dashboardPort -gt 65535) {
    throw 'OLLAMA_DASHBOARD_PORT must be between 1 and 65535.'
}

$lastPort = [Math]::Min($dashboardPort + 20, 65535)
$selectedPort = $null
$dashboardAlreadyRunning = $false

for ($candidatePort = $dashboardPort; $candidatePort -le $lastPort; $candidatePort++) {
    $candidateUrl = "http://127.0.0.1:$candidatePort/"
    try {
        $health = Invoke-RestMethod -Uri "${candidateUrl}api/settings" -TimeoutSec 1
        if ($health.PSObject.Properties.Name -contains 'gatewayEnabled') {
            $selectedPort = $candidatePort
            $dashboardAlreadyRunning = $true
            break
        }
    }
    catch {
    }

    $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $candidatePort)
    try {
        $listener.Start()
        $listener.Stop()
        $selectedPort = $candidatePort
        break
    }
    catch {
        $listener.Stop()
    }
}

if ($null -eq $selectedPort) {
    throw "No available dashboard port found between $dashboardPort and $lastPort."
}

$dashboardPort = $selectedPort
$env:OLLAMA_DASHBOARD_PORT = [string]$dashboardPort
$dashboardUrl = "http://127.0.0.1:$dashboardPort/"

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

if (-not $dashboardAlreadyRunning) {
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
