# Stop processes listening on test-runner dev ports (5175 Vite, 8767 API).
param(
    [int[]]$Ports = @(5175, 8767)
)

$stopped = @()
foreach ($port in $Ports) {
    $conns = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
    foreach ($conn in $conns) {
        $procId = $conn.OwningProcess
        if (-not $procId -or $procId -eq $PID) { continue }
        if ($stopped -contains $procId) { continue }
        $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
        $name = if ($proc) { $proc.ProcessName } else { "pid:$procId" }
        Write-Host "Stopping $name (PID $procId) on port $port"
        Stop-Process -Id $procId -Force -ErrorAction SilentlyContinue
        $stopped += $procId
    }
}

if ($stopped.Count -gt 0) {
    Start-Sleep -Milliseconds 600
}
