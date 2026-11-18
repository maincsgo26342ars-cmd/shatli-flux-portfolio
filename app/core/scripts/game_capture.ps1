param(
    [Parameter(Mandatory=$true)][string]$PresentMon,
    [Parameter(Mandatory=$true)][string]$CsvPath,
    [Parameter(Mandatory=$true)][string]$StatusPath,
    [Parameter(Mandatory=$true)][string]$CancelPath,
    [Parameter(Mandatory=$true)][string]$StdOutPath,
    [Parameter(Mandatory=$true)][string]$StdErrPath,
    [Parameter(Mandatory=$true)][int]$Seconds,
    [Parameter(Mandatory=$true)][int]$Delay
)
# Elevated only for ETW capture. No game files, registry, input or network access.
$ErrorActionPreference = 'Stop'
$capture = $null
$stdOutTask = $null
$stdErrTask = $null
function Write-State([string]$State, [int]$ExitCode = -1, [string]$Detail = '') {
    @{
        state = $State
        pid = $PID
        exit_code = $ExitCode
        detail = $Detail
    } | ConvertTo-Json -Compress | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}
function Save-Output {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    if ($null -ne $stdOutTask) {
        [System.IO.File]::WriteAllText($StdOutPath, $stdOutTask.GetAwaiter().GetResult(), $encoding)
    }
    if ($null -ne $stdErrTask) {
        [System.IO.File]::WriteAllText($StdErrPath, $stdErrTask.GetAwaiter().GetResult(), $encoding)
    }
}
try {
    if (-not (Test-Path -LiteralPath $PresentMon -PathType Leaf)) { throw 'PresentMon not found' }
    if ($Seconds -lt 5 -or $Seconds -gt 300 -or $Delay -lt 0 -or $Delay -gt 120) { throw 'Invalid duration' }
    $arguments = @(
        '--process_name', 'cs2.exe',
        '--output_file', ('"' + $CsvPath + '"'),
        '--timed', [string]$Seconds,
        '--delay', [string]$Delay,
        '--terminate_after_timed',
        '--terminate_on_proc_exit',
        '--exclude_dropped',
        '--no_console_stats',
        '--no_track_input',
        '--session_name', ('ShatliFlux_' + [string]$PID)
    )
    # System.Diagnostics.Process reliably exposes ExitCode in Windows PowerShell 5.1.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $PresentMon
    $startInfo.Arguments = $arguments -join ' '
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    # Native PresentMon uses the system ANSI encoding when stdout/stderr are pipes.
    $capture = New-Object System.Diagnostics.Process
    $capture.StartInfo = $startInfo
    if (-not $capture.Start()) { throw 'PresentMon could not be started' }
    $stdOutTask = $capture.StandardOutput.ReadToEndAsync()
    $stdErrTask = $capture.StandardError.ReadToEndAsync()
    Write-State 'running'
    while (-not $capture.HasExited) {
        if (Test-Path -LiteralPath $CancelPath -PathType Leaf) {
            Stop-Process -Id $capture.Id -ErrorAction SilentlyContinue
            $capture.WaitForExit(5000)
            if ($capture.HasExited) { try { Save-Output } catch {} }
            Write-State 'cancelled' 23
            exit 23
        }
        Start-Sleep -Milliseconds 200
        $capture.Refresh()
    }
    $capture.WaitForExit()
    Save-Output
    if (Test-Path -LiteralPath $CancelPath -PathType Leaf) {
        Write-State 'cancelled' 23
        exit 23
    }
    $exitCode = $capture.ExitCode
    if ($null -eq $exitCode) { throw 'PresentMon exit code unavailable' }
    if ($exitCode -ne 0) { throw "PresentMon exit code $exitCode" }
    Write-State 'completed' $exitCode
    exit 0
} catch {
    $detail = $_.Exception.Message
    if ($null -ne $capture -and -not $capture.HasExited) {
        Stop-Process -Id $capture.Id -ErrorAction SilentlyContinue
        $capture.WaitForExit(5000)
    }
    if ($null -ne $capture -and $capture.HasExited) { try { Save-Output } catch {} }
    if (Test-Path -LiteralPath $CancelPath -PathType Leaf) {
        Write-State 'cancelled' 23 $detail
        exit 23
    }
    Write-State 'failed' 1 $detail
    exit 1
}
