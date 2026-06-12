# Registers the Coherent Outreach agent to run at every Windows login.
# Run once in PowerShell:  .\install_agent.ps1
$ErrorActionPreference = "Stop"

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$repo = Split-Path -Parent $here

# Prefer the backend venv's windowless python (no console flashes on boot).
$pythonw = Join-Path $repo "backend\.venv\Scripts\pythonw.exe"
if (-not (Test-Path $pythonw)) {
  $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
  if ($cmd) { $pythonw = $cmd.Source } else { throw "pythonw.exe not found; create the backend venv first." }
}

$agent = Join-Path $here "coherent_agent.py"
if (-not (Test-Path $agent)) { throw "coherent_agent.py not found at $agent" }

# Seed a config file from the example if one doesn't exist yet.
$cfg = Join-Path $here "agent.config.json"
if (-not (Test-Path $cfg)) {
  Copy-Item (Join-Path $here "agent.config.example.json") $cfg
  Write-Host "Created agent.config.json (edit it if Chrome is in a non-standard path)."
}

$action  = New-ScheduledTaskAction -Execute $pythonw -Argument "`"$agent`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "CoherentOutreachAgent" -Action $action -Trigger $trigger `
  -Settings $settings -Force `
  -Description "Launches Coherent Outreach (backend + Chrome + LinkedIn) at login." | Out-Null

Write-Host "Registered scheduled task 'CoherentOutreachAgent' (runs at login)."
Write-Host "Start it now:    Start-ScheduledTask -TaskName CoherentOutreachAgent"
Write-Host "Remove it later: Unregister-ScheduledTask -TaskName CoherentOutreachAgent -Confirm:`$false"
