$ErrorActionPreference = "Stop"

$lines = netstat -ano | Select-String ":8000"
if (-not $lines) {
  Write-Output "No process found listening on port 8000."
  exit 0
}

$pids = @()
foreach ($line in $lines) {
  $text = ($line.ToString() -replace '\s+', ' ').Trim()
  if ($text -match 'LISTENING ([0-9]+)$') {
    $pids += [int]$Matches[1]
  }
}

$pids = @($pids | Select-Object -Unique)
if (-not $pids -or $pids.Count -eq 0) {
  Write-Output "No listening process found on port 8000."
  exit 0
}

foreach ($portPid in $pids) {
  Stop-Process -Id $portPid -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 500
$remaining = netstat -ano | Select-String ':8000'
if ($remaining) {
  $left = @()
  foreach ($line in $remaining) {
    $text = ($line.ToString() -replace '\s+', ' ').Trim()
    if ($text -match 'LISTENING ([0-9]+)$') {
      $left += [int]$Matches[1]
    }
  }
  $left = @($left | Select-Object -Unique)
  if ($left.Count -gt 0) {
    Write-Error ("Port 8000 is still in use by: " + ($left -join ', '))
    exit 1
  }
}

Write-Output ("Stopped processes on port 8000: " + ($pids -join ', '))
