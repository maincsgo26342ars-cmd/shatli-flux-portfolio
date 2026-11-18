param([int]$Seconds = 10)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$Seconds = [Math]::Max(2, [Math]::Min(60, $Seconds))
$cpu = [System.Collections.Generic.List[double]]::new()
$memory = [System.Collections.Generic.List[double]]::new()
$processes = [System.Collections.Generic.List[double]]::new()
$top = @()
for ($index = 0; $index -lt $Seconds; $index++) {
    try {
        $sample = Get-CimInstance Win32_PerfFormattedData_PerfOS_Processor -Filter "Name='_Total'"
        $cpu.Add([double]$sample.PercentProcessorTime)
    } catch {}
    try {
        $os = Get-CimInstance Win32_OperatingSystem
        if ([double]$os.TotalVisibleMemorySize -gt 0) {
            $used = 100.0 * (1.0 - [double]$os.FreePhysicalMemory / [double]$os.TotalVisibleMemorySize)
            $memory.Add($used)
        }
    } catch {}
    try {
        $running = @(Get-Process -ErrorAction SilentlyContinue)
        $processes.Add([double]$running.Count)
        $top = @($running | Sort-Object WorkingSet64 -Descending | Select-Object -First 5 |
            ForEach-Object { @{ name = $_.ProcessName; memory_mb = [Math]::Round($_.WorkingSet64 / 1MB, 1) } })
    } catch {}
    Start-Sleep -Seconds 1
    Write-Output "PROGRESS $($index + 1) $Seconds"
}
function Average($values) {
    if ($values.Count -eq 0) { return $null }
    return [Math]::Round(($values | Measure-Object -Average).Average, 2)
}
function Percentile95($values) {
    if ($values.Count -eq 0) { return $null }
    $sorted = @($values | Sort-Object)
    return [Math]::Round([double]$sorted[[Math]::Floor(0.95 * ($sorted.Count - 1))], 2)
}
$result = [ordered]@{
    samples = $Seconds
    duration_seconds = $Seconds
    cpu_average_percent = Average $cpu
    cpu_p95_percent = Percentile95 $cpu
    memory_used_percent = Average $memory
    process_count_average = Average $processes
    top_processes = $top
}
Write-Output ("RESULT " + ($result | ConvertTo-Json -Compress -Depth 4))
