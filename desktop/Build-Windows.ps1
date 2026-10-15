$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$localPython = Join-Path $projectRoot '.venv-local/Scripts/python.exe'
if (!(Test-Path -LiteralPath $localPython)) { throw 'Execute desktop/Install-Local.ps1 antes de empacotar.' }
& $localPython -m pip install pyinstaller==6.22.3
if ($LASTEXITCODE -ne 0) { throw 'Nao foi possivel instalar o empacotador.' }
$bundleArguments = @()
foreach ($toolName in @('ffmpeg','ffprobe','node')) {
    $toolCommand = Get-Command $toolName -ErrorAction Stop
    $bundleArguments += @('--add-binary', "$($toolCommand.Source);bin")
}
& $localPython -m PyInstaller --noconfirm --clean --windowed --onedir --name FBR-Transcript-Local --paths $projectRoot --distpath desktop/dist --workpath desktop/build --specpath desktop/build --collect-all yt_dlp --collect-all yt_dlp_ejs @bundleArguments desktop/launch.py
if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar o aplicativo portatil.' }
$portableRoot = Join-Path $projectRoot 'desktop/dist/FBR-Transcript-Local'
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'README.md') -Destination (Join-Path $portableRoot 'LEIA-ME.md')
$licenseRoot = Join-Path $portableRoot 'licenses'
New-Item -ItemType Directory -Force -Path $licenseRoot | Out-Null
$ffmpegRoot = Split-Path -Parent (Split-Path -Parent (Get-Command ffmpeg).Source)
if (Test-Path -LiteralPath (Join-Path $ffmpegRoot 'LICENSE')) {
    Copy-Item -LiteralPath (Join-Path $ffmpegRoot 'LICENSE') -Destination (Join-Path $licenseRoot 'FFmpeg-LICENSE.txt')
}
$nodeVersion = (& node --version).Trim()
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/nodejs/node/$nodeVersion/LICENSE" -OutFile (Join-Path $licenseRoot 'Node-LICENSE.txt')
@'
Componentes: Python, Tcl/Tk, yt-dlp, yt-dlp-ejs, httpx e dependencias, FFmpeg e Node.js.
As licencas dos pacotes Python acompanham seus metadados quando incluidos pelo empacotador.
FFmpeg e fontes da distribuicao utilizada: https://www.gyan.dev/ffmpeg/builds/
Node.js e fontes: https://github.com/nodejs/node
yt-dlp: https://github.com/yt-dlp/yt-dlp
Codigo do auxiliar: https://github.com/appbigwriter/transcript/tree/main/desktop
'@ | Set-Content -LiteralPath (Join-Path $licenseRoot 'COMPONENTES.txt') -Encoding UTF8
Write-Host 'Aplicativo criado em desktop/dist/FBR-Transcript-Local/FBR-Transcript-Local.exe'
