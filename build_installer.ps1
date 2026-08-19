$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$ffmpeg = "C:\ffmpeg\bin\ffmpeg.exe"
$ffprobe = "C:\ffmpeg\bin\ffprobe.exe"
$node = "C:\Program Files\nodejs\node.exe"
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"
$iconPng = ".\icone.png"
$iconIco = ".\icone.ico"

if (-not (Test-Path -LiteralPath ".\youtube_pot_provider\server\build\generate_once.js")) {
    git clone --single-branch --branch 1.3.1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git youtube_pot_provider
    Push-Location ".\youtube_pot_provider\server"
    npm ci
    npx tsc
    Pop-Location
}

foreach ($requiredFile in @($ffmpeg, $ffprobe, $node, $iscc, $iconPng, $iconIco, ".\baixador_multiplos.py", ".\version_info.txt", ".\installer.iss")) {
    if (-not (Test-Path -LiteralPath $requiredFile)) {
        throw "Arquivo necessário não encontrado: $requiredFile"
    }
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name "YoutubeToPC" `
    --version-file ".\version_info.txt" `
    --icon $iconIco `
    --collect-all "yt_dlp" `
    --collect-all "yt_dlp_ejs" `
    --collect-all "yt_dlp_plugins" `
    --add-data "$iconPng;." `
    --add-data "$iconIco;." `
    --add-binary "$ffmpeg;ffmpeg" `
    --add-binary "$ffprobe;ffmpeg" `
    --add-binary "$node;node" `
    --add-data "$projectDir\youtube_pot_provider\server;youtube_pot_provider\server" `
    ".\baixador_multiplos.py"

if ($LASTEXITCODE -ne 0) {
    throw "O PyInstaller encerrou com código $LASTEXITCODE"
}

& $iscc ".\installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "O Inno Setup encerrou com código $LASTEXITCODE"
}

Write-Host "Instalador criado em: $projectDir\release\YoutubeToPC_Setup_1.0.0.exe"
