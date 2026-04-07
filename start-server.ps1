$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir

function Get-Port8000Pids {
  $lines = netstat -ano | Select-String ":8000"
  if (-not $lines) { return @() }

  $pids = @()
  foreach ($line in $lines) {
    $text = ($line.ToString() -replace '\s+', ' ').Trim()
    if ($text -match 'LISTENING ([0-9]+)$') {
      $pids += [int]$Matches[1]
    }
  }

  return @($pids | Select-Object -Unique)
}

$localPython = Join-Path $appDir '.tools\python\python.exe'
if (Test-Path $localPython) {
  $pythonExe = $localPython
} else {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
  if (-not $python) {
    Write-Error "Python is not installed or not on PATH, and local portable Python was not found."
    exit 1
  }
  $pythonExe = $python.Source
}

$existingPids = Get-Port8000Pids
if ($existingPids.Count -gt 0) {
  foreach ($existingPid in $existingPids) {
    Stop-Process -Id $existingPid -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 500
}

$stillListening = Get-Port8000Pids
if ($stillListening.Count -gt 0) {
  Write-Error ("Could not free port 8000. Still listening: " + ($stillListening -join ', '))
  exit 1
}

$cmdArgs = '/c start "dndtracker-server" /b """' + $pythonExe + '""" server.py'
$launcher = Start-Process -FilePath 'cmd.exe' -ArgumentList $cmdArgs -WorkingDirectory $appDir -WindowStyle Hidden -PassThru

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 300
  try {
    $resp = Invoke-WebRequest -Uri 'http://127.0.0.1:8000/' -UseBasicParsing -TimeoutSec 2
    if ($resp.StatusCode -ge 200) { $ok = $true; break }
  } catch {}
}

if ($ok) {
  $listeners = Get-Port8000Pids
  if ($listeners.Count -ne 1) {
    Write-Warning ("Port 8000 is responding, listener PID(s) are: " + ($listeners -join ', ') + ".")
  }
  try { Start-Process 'http://127.0.0.1:8000/' } catch {}
  Write-Output ("Server started. Launcher PID " + $launcher.Id + ". URL: http://127.0.0.1:8000/")
  exit 0
}

Write-Error "Server process started but port 8000 did not respond yet."
exit 1
