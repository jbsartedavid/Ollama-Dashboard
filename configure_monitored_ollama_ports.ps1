[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param()

$ollamaHost = '127.0.0.1:11435'
$dashboardPort = '11434'
$userEnvironment = [EnvironmentVariableTarget]::User

if ($PSCmdlet.ShouldProcess(
        'Current user environment',
        "Set Ollama backend to $ollamaHost and dashboard proxy to port $dashboardPort"
    )) {
    [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $ollamaHost, $userEnvironment)
    [Environment]::SetEnvironmentVariable('OLLAMA_DASHBOARD_PORT', $dashboardPort, $userEnvironment)
    $env:OLLAMA_HOST = $ollamaHost
    $env:OLLAMA_DASHBOARD_PORT = $dashboardPort

    Write-Output 'Saved the Ollama and dashboard port settings for this Windows user.'
    Write-Output 'This script did not stop or restart any process.'
    Write-Output 'Fully exit Ollama Desktop; it does not honor the custom backend port.'
    Write-Output 'Start standalone ollama.exe serve on 127.0.0.1:11435, then start Ollama Dashboard.'
    Write-Output 'The dashboard requires port 11434 for transparent monitoring and will not silently fall back to another port.'
    Write-Output 'Clients can keep using the same server address and port 11434; the dashboard forwards requests to Ollama on 11435.'
}
