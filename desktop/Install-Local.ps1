$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
if (!(Test-Path -LiteralPath '.venv-local/Scripts/python.exe')) {
    & py -3 -m venv .venv-local
    if ($LASTEXITCODE -ne 0) { throw 'Instale Python 3.10 ou superior com Tcl/Tk antes de continuar.' }
}
$localPython = Join-Path $projectRoot '.venv-local/Scripts/python.exe'
& $localPython -m pip install -r desktop/requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar as dependencias.' }
if (!(Get-Command ffmpeg -ErrorAction SilentlyContinue) -or !(Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Host 'Instale FFmpeg (incluindo ffprobe) e adicione ao PATH para habilitar a alternativa por audio.'
}
Write-Host 'Ambiente instalado. Execute desktop/Start-Local.ps1 para abrir o aplicativo.'
