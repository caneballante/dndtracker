$ErrorActionPreference = "Stop"
$appDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $appDir

function Get-Port8000Pids {
  $lines = netstat -ano | Select-String ':8000\s'
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
  Write-Output 'Port 8000 is already in use. No process was stopped and no extra recording tab was opened.'
  Write-Output 'If this is the tracker, keep using its existing tab. To apply an update, stop recording and save all pending audio before explicitly stopping and restarting the server.'
  exit 0
}

$stillListening = Get-Port8000Pids
if ($stillListening.Count -gt 0) {
  Write-Error ("Could not free port 8000. Still listening: " + ($stillListening -join ', '))
  exit 1
}

$logDir = Join-Path $appDir '.pycache_tmp\server-logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$runStamp = Get-Date -Format 'yyyyMMdd-HHmmss-fff'
$launcher = Start-Process -FilePath $pythonExe -ArgumentList @('-u', 'server.py') -WorkingDirectory $appDir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "$runStamp.out.log") -RedirectStandardError (Join-Path $logDir "$runStamp.err.log")

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
