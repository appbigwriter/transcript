$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$localPython = Join-Path $projectRoot '.venv-local/Scripts/pythonw.exe'
if (!(Test-Path -LiteralPath $localPython)) { throw 'Execute primeiro desktop/Install-Local.ps1.' }
Start-Process -FilePath $localPython -ArgumentList '-m','desktop' -WorkingDirectory $projectRoot -WindowStyle Normal
