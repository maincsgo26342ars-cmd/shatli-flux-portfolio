$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$result = [ordered]@{
    cpu = $null; gpu = $null; ram_bytes = $null; windows = $null
    cpu_logical = $null; ram_available_bytes = $null; power_plan = $null
    game_mode = $null; hags = $null; startup_count = $null
    battery_present = $null; system_drive_free_bytes = $null
}
try {
    $processors = @(Get-CimInstance Win32_Processor)
    $result.cpu = ($processors.Name -join ' / ')
    $result.cpu_logical = ($processors | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
} catch {}
try { $result.gpu = (@(Get-CimInstance Win32_VideoController).Name -join ' / ') } catch {}
try { $result.ram_bytes = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory } catch {}
try {
    $os = Get-CimInstance Win32_OperatingSystem
    $result.windows = "$($os.Caption) (build $($os.BuildNumber))"
    $result.ram_available_bytes = [int64]$os.FreePhysicalMemory * 1024
} catch {}
try {
    $plan = Get-CimInstance Win32_PowerPlan -Namespace root\cimv2\power -Filter 'IsActive = 1'
    $result.power_plan = $plan.ElementName
} catch {}
if (-not $result.power_plan) {
    try {
        $active = (& "$env:SystemRoot\System32\powercfg.exe" /getactivescheme) -join ' '
        if ($LASTEXITCODE -eq 0 -and $active.Contains(':')) {
            $result.power_plan = ($active -split ':', 2)[1].Trim()
        }
    } catch {}
}
try { $result.game_mode = (Get-ItemProperty -Path 'HKCU:\Software\Microsoft\GameBar' -Name 'AutoGameModeEnabled').AutoGameModeEnabled } catch {}
try { $result.hags = (Get-ItemProperty -Path 'HKLM:\SYSTEM\CurrentControlSet\Control\GraphicsDrivers' -Name 'HwSchMode').HwSchMode } catch {}
try {
    $startup = @()
    $startup += @((Get-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run').GetValueNames())
    $startup += @((Get-Item 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Run').GetValueNames())
    $result.startup_count = @($startup | Sort-Object -Unique).Count
} catch {}
try { $result.battery_present = @(Get-CimInstance Win32_Battery).Count -gt 0 } catch {}
try { $result.system_drive_free_bytes = (Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($env:SystemDrive)'").FreeSpace } catch {}
$result | ConvertTo-Json -Compress
