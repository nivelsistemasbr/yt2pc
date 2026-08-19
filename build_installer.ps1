$ErrorActionPreference = "Stop"
$projectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectDir

$ffmpeg = "C:\ffmpeg\bin\ffmpeg.exe"
$ffprobe = "C:\ffmpeg\bin\ffprobe.exe"
$iscc = Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe"
$iconPng = ".\icone.png"
$iconIco = ".\icone.ico"

foreach ($requiredFile in @($ffmpeg, $ffprobe, $iscc, $iconPng, $iconIco, ".\baixador_multiplos.py", ".\version_info.txt", ".\installer.iss")) {
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
    --add-data "$iconPng;." `
    --add-data "$iconIco;." `
    --add-binary "$ffmpeg;ffmpeg" `
    --add-binary "$ffprobe;ffmpeg" `
    ".\baixador_multiplos.py"

if ($LASTEXITCODE -ne 0) {
    throw "O PyInstaller encerrou com código $LASTEXITCODE"
}

& $iscc ".\installer.iss"
if ($LASTEXITCODE -ne 0) {
    throw "O Inno Setup encerrou com código $LASTEXITCODE"
}

Write-Host "Instalador criado em: $projectDir\release\YoutubeToPC_Setup_1.0.0.exe"
