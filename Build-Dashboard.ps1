$ErrorActionPreference = 'Stop'
$projectDir = $PSScriptRoot
Set-Location -LiteralPath $projectDir
$pythonPath = Join-Path $projectDir '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    & py -3.12 -m venv (Join-Path $projectDir '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create project Python environment' }
}
& $pythonPath -m pip install --disable-pip-version-check -r requirements-build.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $pythonPath -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw 'Tests failed; no executable built' }
# Stop only our local dashboard before replacing its running executable.
& $pythonPath run_dashboard.py --stop --no-browser | Out-Null
$targetExe = Join-Path $projectDir 'dist\KV-Agent-Desk.exe'
$stopDeadline = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $stopDeadline) {
    $taskProcesses = @(Get-Process -Name 'KV-Agent-Desk' -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $targetExe })
    if ($taskProcesses.Count -eq 0) { break }
    Start-Sleep -Milliseconds 200
}
if ($taskProcesses.Count -gt 0) { throw 'Dashboard has not exited; executable was not replaced' }
& $pythonPath -m PyInstaller --noconsole --onefile --name KV-Agent-Desk --noconfirm --icon agent_dashboard/assets/app.ico --hidden-import agent_dashboard.adapters.codex --hidden-import agent_dashboard.adapters.zcode --hidden-import agent_dashboard.adapters.zcode_quota --hidden-import agent_dashboard.adapters.antigravity --hidden-import webview.platforms.winforms --hidden-import webview.platforms.edgechromium --hidden-import pystray._win32 --add-data 'agent_dashboard/assets/app.ico;agent_dashboard/assets' --add-data 'agent_dashboard/assets/deepseek.png;agent_dashboard/assets' run_dashboard.py
if ($LASTEXITCODE -ne 0) { throw 'Executable build failed' }
Write-Output (Join-Path $projectDir 'dist\KV-Agent-Desk.exe')
