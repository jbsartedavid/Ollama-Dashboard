[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateRange(1, 65535)]
    [int]$DashboardPort = 5111
)

$dashboardPortValue = $DashboardPort.ToString()
$userEnvironment = [EnvironmentVariableTarget]::User

if ($PSCmdlet.ShouldProcess(
        'Current user environment',
        "Set dashboard port to $dashboardPortValue and restore Ollama's default listener"
    )) {
    [Environment]::SetEnvironmentVariable('OLLAMA_HOST', $null, $userEnvironment)
    [Environment]::SetEnvironmentVariable('OLLAMA_DASHBOARD_PORT', $dashboardPortValue, $userEnvironment)
    Remove-Item Env:OLLAMA_HOST -ErrorAction SilentlyContinue
    $env:OLLAMA_DASHBOARD_PORT = $dashboardPortValue

    if (-not ('OllamaDashboard.EnvironmentNotifier' -as [type])) {
        Add-Type @'
using System;
using System.Runtime.InteropServices;

public static class OllamaDashboardEnvironmentNotifier
{
    [DllImport("user32.dll", CharSet = CharSet.Auto, SetLastError = true)]
    public static extern IntPtr SendMessageTimeout(
        IntPtr hWnd, UInt32 message, UIntPtr wParam, string lParam,
        UInt32 flags, UInt32 timeout, out UIntPtr result);
}
'@
    }
    [UIntPtr]$broadcastResult = [UIntPtr]::Zero
    [OllamaDashboardEnvironmentNotifier]::SendMessageTimeout(
        [IntPtr]0xffff, 0x001A, [UIntPtr]::Zero, 'Environment', 0x0002, 5000,
        [ref]$broadcastResult
    ) | Out-Null

    Write-Output "Saved dashboard port $dashboardPortValue and removed the custom Ollama listener override."
    Write-Output 'This script did not stop or restart any process.'
    Write-Output 'Ollama can continue using its standard port 11434, and existing direct clients need no URL change.'
    Write-Output "Only clients pointed at the dashboard proxy address on port $dashboardPortValue will appear in dashboard request history."
    Write-Output 'Restart the dashboard to apply the selected port.'
}
