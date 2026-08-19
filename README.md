# Youtube to PC

Aplicativo Windows com interface gráfica para baixar vídeos, áudio e metadados públicos de URLs compatíveis com o [yt-dlp](https://github.com/yt-dlp/yt-dlp). Cada mídia concluída recebe também um arquivo `.txt` com título, canal, URL, data, duração e descrição.

## Uso

1. Abra `YoutubeToPC.exe` ou instale `YoutubeToPC_Setup_1.0.0.exe`.
2. Cole uma ou mais URLs, uma por linha.
3. Escolha o formato e a pasta de destino.
4. Clique em **Iniciar download**.

Os formatos disponíveis são:

- **Melhor vídeo (MKV):** combina as melhores faixas de vídeo e áudio disponíveis.
- **Vídeo compatível (MP4):** prioriza faixas MP4/M4A para melhor compatibilidade.
- **Somente áudio (MP3):** extrai o áudio em MP3.

Vídeos longos e em alta resolução podem ter arquivos grandes. Por exemplo, um set de cerca de 1h20 em qualidade alta pode ultrapassar 2 GB.

## Requisitos para desenvolvimento

- Windows
- Python 3.13 ou superior
- FFmpeg e FFprobe em `C:\ffmpeg\bin`
- Node.js 22 ou superior (usado para os desafios do YouTube)
- Inno Setup 7 para gerar o instalador

Instale a dependência Python:

```powershell
python -m pip install -U "yt-dlp[default]" bgutil-ytdlp-pot-provider pyinstaller
```

## Executar pelo código-fonte

```powershell
python .\baixador_multiplos.py
```

## Gerar o instalador

```powershell
.\build_installer.ps1
```

O instalador é criado em `release\YoutubeToPC_Setup_1.0.0.exe`. Ele contém o aplicativo, o yt-dlp e o FFmpeg, sem exigir instalação separada desses componentes pelo usuário final.

## Solução de problemas

- **Download não inicia ou falha:** atualize o aplicativo. Ele faz novas tentativas para falhas temporárias de conexão e exibe a mensagem retornada pelo serviço quando não conseguir concluir.
- **Vídeo privado, com restrição de idade ou de região:** o download pode exigir login no serviço de origem ou pode não estar disponível.
- **Download muito lento:** teste o modo MP3 ou MP4 e confira espaço livre na pasta de destino.
- **Falha ao combinar vídeo e áudio:** use a versão distribuída pelo instalador, que já inclui FFmpeg.

Use o aplicativo apenas para baixar conteúdo que você tem autorização para salvar e de acordo com os termos da plataforma de origem.
