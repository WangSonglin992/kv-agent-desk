@echo off
setlocal
cd /d "%~dp0"
set "KV_REGISTERED_EXE="
for /f "usebackq delims=" %%I in (`powershell.exe -NoProfile -NonInteractive -Command "$ErrorActionPreference='Stop'; try { $ini=Join-Path ([Environment]::GetFolderPath('UserProfile')) '.kv-agent-desk\installation.ini'; $dir=$null; $invalid=$false; $iniExists=$true; try{$bytes=[IO.File]::ReadAllBytes($ini)}catch [IO.FileNotFoundException]{$iniExists=$false}catch [IO.DirectoryNotFoundException]{$iniExists=$false}; if($iniExists){ if ($bytes.Length -lt 2 -or -not (($bytes[0] -eq 255 -and $bytes[1] -eq 254) -or ($bytes[0] -eq 254 -and $bytes[1] -eq 255))) { $invalid=$true } else { $encoding=if($bytes[0] -eq 254){[Text.Encoding]::BigEndianUnicode}else{[Text.Encoding]::Unicode}; $text=$encoding.GetString($bytes,2,$bytes.Length-2); $section=''; $sectionCount=0; $values=@{}; foreach($line in ($text -split '\r?\n')) { $line=$line.Trim(); if($line -match '^\[(.+)\]$') { $section=$Matches[1]; $sectionCount++; continue }; if($section -eq 'Installation' -and $line -match '^([^=]+)=(.*)$') { $key=$Matches[1].Trim(); if($key -cnotin @('InstallDir','DesktopShortcut','InstallerManaged') -or $values.ContainsKey($key)){$invalid=$true}else{$values[$key]=$Matches[2].Trim()}; continue }; if($section -eq 'Installation' -and $line -and $line -notmatch '^[;#]'){$invalid=$true} }; if($invalid -or $sectionCount -ne 1 -or $section -cne 'Installation' -or $values.Count -ne 3 -or -not $values.ContainsKey('InstallDir') -or $values['DesktopShortcut'] -notin @('0','1') -or $values['InstallerManaged'] -notin @('0','1')){$invalid=$true}else{$dir=$values['InstallDir']} } } else { $key=[Microsoft.Win32.Registry]::CurrentUser.OpenSubKey('Software\KV-Agent-Desk'); if($key){try{if($key.GetValueKind('InstallDir') -ne [Microsoft.Win32.RegistryValueKind]::String){$invalid=$true}else{$dir=$key.GetValue('InstallDir',$null,[Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)}}finally{$key.Dispose()}} }; if($invalid){Write-Output '__INVALID_INSTALLATION_RECORD__';exit 0}; if($null -ne $dir){$full=[IO.Path]::GetFullPath($dir);$root=[IO.Path]::GetPathRoot($full);if(-not [IO.Path]::IsPathRooted($dir) -or $dir -match '^[A-Za-z]:(?![\\/])' -or ($dir.StartsWith('\') -and -not $dir.StartsWith('\\')) -or [string]::IsNullOrWhiteSpace($root) -or $full.TrimEnd('\') -ieq $root.TrimEnd('\')){Write-Output '__INVALID_INSTALLATION_RECORD__';exit 0};$exe=Join-Path $full 'KV-Agent-Desk.exe';if(Test-Path -LiteralPath $exe -PathType Leaf){Write-Output $exe}else{Write-Output '__REGISTERED_INSTALL_MISSING__'}} } catch { Write-Output '__INVALID_INSTALLATION_RECORD__' }"`) do set "KV_REGISTERED_EXE=%%I"
if /i "%KV_REGISTERED_EXE%"=="__INVALID_INSTALLATION_RECORD__" (
  echo The registered KV Agent Desk installation record is invalid.
  exit /b 1
)
if /i "%KV_REGISTERED_EXE%"=="__REGISTERED_INSTALL_MISSING__" (
  echo The registered KV Agent Desk installation could not be found.
  exit /b 1
)
if defined KV_REGISTERED_EXE (
  start "" "%KV_REGISTERED_EXE%" %*
  exit /b 0
)
if exist "D:\APP\KV-Agent-Desk\KV-Agent-Desk.exe" (
  start "" "D:\APP\KV-Agent-Desk\KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%LOCALAPPDATA%\Programs\KV-Agent-Desk\KV-Agent-Desk.exe" (
  start "" "%LOCALAPPDATA%\Programs\KV-Agent-Desk\KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%~dp0KV-Agent-Desk.exe" (
  start "" "%~dp0KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%~dp0dist\KV-Agent-Desk.exe" (
  start "" "%~dp0dist\KV-Agent-Desk.exe" %*
  exit /b 0
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" "%~dp0run_dashboard.py" %*
  exit /b 0
)
echo Build the app first with powershell -File Build-Dashboard.ps1
pause
exit /b 1
