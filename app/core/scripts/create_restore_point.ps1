# Fixed operation only. No arguments, downloads, policy bypass or registry edits.
$ErrorActionPreference = 'Stop'
$WarningPreference = 'Stop'
try {
    $description = 'Shatli FLUX ' + [Guid]::NewGuid().ToString()
    Checkpoint-Computer -Description $description -RestorePointType MODIFY_SETTINGS
    $created = @(Get-ComputerRestorePoint | Where-Object { $_.Description -eq $description })
    if ($created.Count -lt 1) { exit 21 }
    exit 0
} catch {
    Write-Host 'Shatli FLUX: restore point was NOT confirmed.'
    Write-Host $_.Exception.Message
    exit 20
}
