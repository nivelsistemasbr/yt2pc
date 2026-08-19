"""Baixador de vídeos com interface gráfica baseado em yt-dlp.

Instale/atualize as dependências com:  python -m pip install -U yt-dlp
Para combinar vídeo e áudio na melhor qualidade, o FFmpeg deve estar instalado.
"""

from __future__ import annotations

import base64
import queue
import re
import threading
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path
from tkinter import END, BOTH, LEFT, RIGHT, W, X, filedialog, messagebox, ttk
import tkinter as tk

import yt_dlp
from yt_dlp.postprocessor.common import PostProcessor


APP_TITLE = "Youtube to PC"
DEFAULT_FOLDER = Path.home() / "Downloads" / APP_TITLE


def runtime_resource(filename: str) -> Path:
    """Localiza arquivos tanto no código-fonte quanto no pacote PyInstaller."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        root = Path(__file__).resolve().parent
    return root / filename


def bundled_ffmpeg_directory() -> Path | None:
    """Retorna a pasta do FFmpeg incluído pelo PyInstaller, quando presente."""
    if getattr(sys, "frozen", False):
        # Em builds onedir recentes, _MEIPASS aponta para a pasta _internal.
        runtime_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        folder = runtime_root / "ffmpeg"
    else:
        folder = Path(__file__).resolve().parent / "ffmpeg"
    return folder if (folder / "ffmpeg.exe").is_file() else None


def bundled_node_path() -> Path | None:
    """Localiza o runtime Node usado nos desafios atuais do YouTube."""
    if getattr(sys, "frozen", False):
        node_path = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "node" / "node.exe"
        return node_path if node_path.is_file() else None
    executable = shutil.which("node")
    return Path(executable) if executable else None


def pot_provider_directory() -> Path | None:
    """Retorna o servidor local que gera PO Tokens para o YouTube."""
    if getattr(sys, "frozen", False):
        root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    else:
        root = Path(__file__).resolve().parent
    folder = root / "youtube_pot_provider" / "server"
    return folder if (folder / "build" / "generate_once.js").is_file() else None


class DownloadCancelled(Exception):
    """Interrompe o yt-dlp de forma controlada quando o usuário cancela."""


class DownloadFailed(RuntimeError):
    """Indica que o yt-dlp não conseguiu concluir um ou mais links."""


class TextMetadataPostProcessor(PostProcessor):
    """Cria um arquivo TXT com os dados públicos do vídeo baixado."""

    def run(self, info):
        video_path = info.get("filepath")
        if not video_path:
            return [], info

        txt_path = Path(video_path).with_suffix(".txt")
        description = info.get("description") or "(Sem descrição disponível.)"
        lines = [
            f"Título: {info.get('title') or 'Sem título'}",
            f"Canal: {info.get('uploader') or info.get('channel') or 'Não informado'}",
            f"URL: {info.get('webpage_url') or info.get('original_url') or 'Não informada'}",
            f"Data de publicação: {format_date(info.get('upload_date'))}",
            f"Duração: {format_duration(info.get('duration'))}",
            "",
            "DESCRIÇÃO",
            "=" * 60,
            description,
            "",
        ]
        txt_path.write_text("\n".join(lines), encoding="utf-8")
        return [], info


def format_date(value: str | None) -> str:
    if value and re.fullmatch(r"\d{8}", value):
        return f"{value[6:8]}/{value[4:6]}/{value[:4]}"
    return value or "Não informada"


def format_duration(value) -> str:
    if value is None:
        return "Não informada"
    seconds = int(value)
    return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"


def clean_urls(raw_text: str) -> list[str]:
    """Extrai URLs, removendo linhas vazias e repetições."""
    urls, seen = [], set()
    for line in raw_text.splitlines():
        url = line.strip()
        if url and url not in seen:
            urls.append(url)
            seen.add(url)
    return urls


def baixar_videos(
    urls: list[str],
    pasta: str | Path = DEFAULT_FOLDER,
    modo: str = "Melhor vídeo (MKV)",
    progress_hook=None,
    logger=None,
):
    """Baixa URLs e cria um TXT de título/descrição para cada mídia concluída."""
    destino = Path(pasta)
    destino.mkdir(parents=True, exist_ok=True)
    # Evita depender da codificação do acento em "áudio". A verificação
    # anterior fazia o modo MP3 baixar o melhor vídeo por engano.
    audio_only = modo.lower().startswith("somente") and "mp3" in modo.lower()
    mp4_output = "MP4" in modo
    if audio_only:
        selected_format = "bestaudio/best"
    elif mp4_output:
        # Prioriza faixas que podem ser combinadas em MP4 sem recodificação.
        # O fallback permite baixar quando o site não oferece essas faixas.
        selected_format = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    else:
        selected_format = "bestvideo*+bestaudio/best"
    options = {
        "format": selected_format,
        "outtmpl": str(destino / "%(title)s [%(resolution)s] [%(id)s].%(ext)s"),
        "noplaylist": False,
        "continuedl": True,
        "ignoreerrors": True,
        "abort_on_error": False,
        # Falhas transitórias do YouTube (timeout, 429 e conexão interrompida)
        # são comuns em vídeos longos. Tentar novamente é mais confiável do que
        # encerrar a fila logo na primeira resposta instável do servidor.
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "socket_timeout": 30,
        "windowsfilenames": True,
        "progress_hooks": [progress_hook] if progress_hook else [],
        "logger": logger,
        "quiet": True,
        "no_warnings": True,
    }
    ffmpeg_directory = bundled_ffmpeg_directory()
    if ffmpeg_directory:
        options["ffmpeg_location"] = str(ffmpeg_directory)
    node_path = bundled_node_path()
    provider_directory = pot_provider_directory()
    original_path = None
    if node_path and provider_directory:
        # O YouTube passou a exigir um PO Token para parte dos downloads. O
        # provedor local gera esse token por vídeo, sem usar credenciais do usuário.
        options["js_runtimes"] = {"node": {"path": str(node_path)}}
        options["extractor_args"] = {
            "youtube": {"player_client": ["mweb"]},
            "youtubepot-bgutilscript": {"server_home": [str(provider_directory)]},
        }
        # O provedor também verifica runtimes opcionais. Alguns ambientes do
        # Windows colocam pontos de montagem bloqueados no PATH; limitar a busca
        # ao Node incluído evita que essa verificação falhe antes do download.
        original_path = os.environ.get("PATH")
        os.environ["PATH"] = str(node_path.parent)
    if audio_only:
        options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]
    elif mp4_output:
        options["merge_output_format"] = "mp4"
        options["postprocessors"] = [
            # O nome "preferedformat" (um r) é mantido assim pela API do yt-dlp.
            {"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"}
        ]
    else:
        options["merge_output_format"] = "mkv"

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.add_post_processor(TextMetadataPostProcessor(), when="after_move")
            return ydl.download(urls)
    finally:
        if original_path is not None:
            os.environ["PATH"] = original_path


class GuiLogger:
    def __init__(self, events: queue.Queue):
        self.events = events
        self.errors: list[str] = []

    def debug(self, message):
        # O progress_hook já entrega estes dados. Enfileirar também as mensagens
        # de debug do yt-dlp pode inundar a fila e deixar a interface atrasada.
        pass

    def warning(self, message):
        self.events.put(("log", f"Aviso: {message}"))

    def error(self, message):
        self.errors.append(str(message))
        self.events.put(("log", f"Erro: {message}"))


class TrafficGraph(tk.Canvas):
    """Gráfico leve de velocidade; todo o desenho ocorre na thread do Tk."""

    def __init__(self, master):
        super().__init__(
            master,
            height=66,
            bg="#111722",
            highlightthickness=0,
            bd=0,
        )
        self.values = deque([0.0], maxlen=70)
        self.bind("<Configure>", lambda _event: self._draw())

    def reset(self):
        self.values.clear()
        self.values.append(0.0)
        self._draw()

    def add(self, bytes_per_second):
        self.values.append(max(0.0, float(bytes_per_second or 0)))
        self._draw()

    def _draw(self):
        self.delete("all")
        width = max(self.winfo_width(), 2)
        height = max(self.winfo_height(), 2)
        for ratio in (0.25, 0.5, 0.75):
            y = height * ratio
            self.create_line(0, y, width, y, fill="#232b3a", dash=(2, 5))

        values = list(self.values)
        if len(values) < 2:
            return
        ceiling = max(max(values) * 1.15, 1.0)
        step = width / (len(values) - 1)
        points = []
        for index, value in enumerate(values):
            points.extend((index * step, height - 5 - (value / ceiling) * (height - 12)))
        area = [0, height, *points, width, height]
        self.create_polygon(area, fill="#252451", outline="")
        self.create_line(*points, fill="#8172ff", width=2, smooth=True)


class DownloaderApp(tk.Tk):
    def __init__(self):
        if os.name == "nt":
            # Faz o Windows usar o ícone próprio na barra de tarefas, em vez do
            # ícone genérico do Python/Tk.
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "YoutubeToPC.Downloader.1"
                )
            except (AttributeError, OSError):
                pass
        super().__init__()
        self.title(APP_TITLE + " — Baixador de vídeos")
        self._apply_window_icon()
        self.geometry("960x760")
        self.minsize(800, 680)
        self.configure(bg="#10131a")
        self.events: queue.Queue = queue.Queue()
        self.cancel_requested = threading.Event()
        self.is_downloading = False
        self.progress_is_pulsing = False
        self.folder_var = tk.StringVar(value=str(DEFAULT_FOLDER))
        self.mode_var = tk.StringVar(value="Melhor vídeo (MKV)")
        self.status_var = tk.StringVar(value="Pronto para receber links")
        self.url_count_var = tk.StringVar(value="0 links")
        self.percent_var = tk.StringVar(value="0%")
        self.detail_var = tk.StringVar(value="Aguardando início")
        self.current_speed_var = tk.StringVar(value="0 B/s")
        self.average_speed_var = tk.StringVar(value="0 B/s")
        self.peak_speed_var = tk.StringVar(value="0 B/s")
        self.transferred_var = tk.StringVar(value="0 B")
        self.traffic_eta_var = tk.StringVar(value="--:--")
        self.speed_samples = deque(maxlen=200)
        self._setup_style()
        self._build_ui()
        self.bind("<Control-Return>", lambda _event: self._start_download())
        self.after(100, self._process_events)

    def _apply_window_icon(self):
        png_path = runtime_resource("icone.png")
        ico_path = runtime_resource("icone.ico")
        try:
            if png_path.is_file():
                # Carregar pelos bytes evita limitações de caminho do Tcl/Tk
                # observadas em algumas instalações do Windows.
                encoded_icon = base64.b64encode(png_path.read_bytes())
                self._window_icon = tk.PhotoImage(data=encoded_icon)
                self.iconphoto(True, self._window_icon)
        except (OSError, tk.TclError):
            # O ícone não deve impedir o aplicativo de abrir em ambientes
            # gráficos que não suportem algum formato específico.
            pass
        try:
            if os.name == "nt" and ico_path.is_file():
                self.iconbitmap(ico_path.as_posix())
        except tk.TclError:
            pass

    def _setup_style(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background="#10131a")
        style.configure("Card.TFrame", background="#191e29")
        style.configure("Title.TLabel", background="#10131a", foreground="#f4f7fb", font=("Segoe UI", 23, "bold"))
        style.configure("Sub.TLabel", background="#10131a", foreground="#9da9bd", font=("Segoe UI", 10))
        style.configure("Label.TLabel", background="#191e29", foreground="#cdd6e5", font=("Segoe UI", 10, "bold"))
        style.configure("Status.TLabel", background="#10131a", foreground="#aeb9ca", font=("Segoe UI", 10))
        style.configure("Badge.TLabel", background="#282446", foreground="#bdb6ff", font=("Segoe UI", 9, "bold"), padding=(10, 5))
        style.configure("Percent.TLabel", background="#191e29", foreground="#ffffff", font=("Segoe UI", 12, "bold"))
        style.configure("Detail.TLabel", background="#191e29", foreground="#9da9bd", font=("Segoe UI", 9))
        style.configure("Traffic.TFrame", background="#111722")
        style.configure("MetricName.TLabel", background="#111722", foreground="#778399", font=("Segoe UI", 8, "bold"))
        style.configure("MetricValue.TLabel", background="#111722", foreground="#eef2f8", font=("Segoe UI", 10, "bold"))
        style.configure("Accent.TButton", background="#6d5dfc", foreground="white", font=("Segoe UI", 10, "bold"), padding=(20, 11), borderwidth=0)
        style.map("Accent.TButton", background=[("active", "#8275ff"), ("disabled", "#46415f")])
        style.configure("Soft.TButton", background="#293144", foreground="#edf1f8", font=("Segoe UI", 10), padding=(12, 8), borderwidth=0)
        style.map("Soft.TButton", background=[("active", "#35405a")])
        style.configure(
            "Format.TCombobox",
            fieldbackground="#0b0f17",
            background="#6d5dfc",
            foreground="#ffffff",
            arrowcolor="#ffffff",
            bordercolor="#6d5dfc",
            lightcolor="#6d5dfc",
            darkcolor="#6d5dfc",
            selectbackground="#6d5dfc",
            selectforeground="#ffffff",
            padding=(11, 9),
            borderwidth=2,
            relief="flat",
        )
        style.map(
            "Format.TCombobox",
            fieldbackground=[("readonly", "#0b0f17"), ("focus", "#121827")],
            foreground=[("readonly", "#ffffff"), ("disabled", "#7d8798")],
            background=[("active", "#8172ff"), ("pressed", "#5546e8"), ("readonly", "#6d5dfc")],
            bordercolor=[("focus", "#a79fff"), ("readonly", "#6d5dfc")],
            arrowcolor=[("disabled", "#7d8798"), ("readonly", "#ffffff")],
        )
        self.option_add("*TCombobox*Listbox.background", "#111722")
        self.option_add("*TCombobox*Listbox.foreground", "#ffffff")
        self.option_add("*TCombobox*Listbox.selectBackground", "#6d5dfc")
        self.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        self.option_add("*TCombobox*Listbox.font", ("Segoe UI", 10))
        style.configure("Horizontal.TProgressbar", troughcolor="#252c3a", background="#6d5dfc", bordercolor="#252c3a", lightcolor="#6d5dfc", darkcolor="#6d5dfc")

    def _build_ui(self):
        header = ttk.Frame(self, padding=(34, 28, 34, 18))
        header.pack(fill=X)
        title_box = ttk.Frame(header)
        title_box.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(title_box, text="Youtube to PC", style="Title.TLabel").pack(anchor=W)
        ttk.Label(title_box, text="Seus vídeos, áudios e informações organizados em um só lugar.", style="Sub.TLabel").pack(anchor=W, pady=(3, 0))
        ttk.Label(header, text="●  ONLINE", style="Badge.TLabel").pack(side=RIGHT, anchor="n", pady=5)

        card = ttk.Frame(self, style="Card.TFrame", padding=22)
        card.pack(fill=BOTH, expand=True, padx=34, pady=(0, 16))
        links_header = ttk.Frame(card, style="Card.TFrame")
        links_header.pack(fill=X)
        links_copy = ttk.Frame(links_header, style="Card.TFrame")
        links_copy.pack(side=LEFT)
        ttk.Label(links_copy, text="LINKS PARA BAIXAR", style="Label.TLabel").pack(anchor=W)
        ttk.Label(links_copy, text="Uma URL por linha; playlists também são aceitas.", style="Detail.TLabel").pack(anchor=W, pady=(3, 9))
        links_actions = ttk.Frame(links_header, style="Card.TFrame")
        links_actions.pack(side=RIGHT, anchor="n")
        ttk.Label(links_actions, textvariable=self.url_count_var, style="Badge.TLabel").pack(side=LEFT, padx=(0, 7))
        ttk.Button(links_actions, text="Colar", style="Soft.TButton", command=self._paste_urls).pack(side=LEFT, padx=(0, 7))
        ttk.Button(links_actions, text="Limpar", style="Soft.TButton", command=self._clear_urls).pack(side=LEFT)
        self.urls_text = tk.Text(card, height=8, bg="#111722", fg="#edf1f8", insertbackground="#ffffff", selectbackground="#6d5dfc", relief="flat", font=("Segoe UI", 10), padx=13, pady=11, wrap="word", highlightthickness=1, highlightbackground="#2a3242", highlightcolor="#7869ff")
        self.urls_text.pack(fill=BOTH, expand=True)
        self.urls_text.bind("<KeyRelease>", self._update_url_count)

        opts = ttk.Frame(card, style="Card.TFrame")
        opts.pack(fill=X, pady=(18, 0))
        ttk.Label(opts, text="FORMATO", style="Label.TLabel").grid(row=0, column=0, sticky=W)
        ttk.Label(opts, text="PASTA DE DESTINO", style="Label.TLabel").grid(row=0, column=1, sticky=W, padx=(18, 0))
        self.mode_box = ttk.Combobox(
            opts,
            state="readonly",
            style="Format.TCombobox",
            font=("Segoe UI", 10, "bold"),
            textvariable=self.mode_var,
            values=(
                "Melhor vídeo (MKV)",
                "Vídeo compatível (MP4)",
                "Somente áudio (MP3)",
            ),
        )
        self.mode_box.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        folder = ttk.Entry(opts, textvariable=self.folder_var, font=("Segoe UI", 10))
        folder.grid(row=1, column=1, sticky="ew", padx=(18, 7), pady=(6, 0))
        ttk.Button(opts, text="Escolher", style="Soft.TButton", command=self._choose_folder).grid(row=1, column=2, pady=(6, 0))
        opts.columnconfigure(0, weight=1)
        opts.columnconfigure(1, weight=3)

        progress_head = ttk.Frame(card, style="Card.TFrame")
        progress_head.pack(fill=X, pady=(18, 7))
        progress_copy = ttk.Frame(progress_head, style="Card.TFrame")
        progress_copy.pack(side=LEFT, fill=X, expand=True)
        ttk.Label(progress_copy, text="PROGRESSO ATUAL", style="Label.TLabel").pack(anchor=W)
        ttk.Label(progress_copy, textvariable=self.detail_var, style="Detail.TLabel").pack(anchor=W, pady=(2, 0))
        ttk.Label(progress_head, textvariable=self.percent_var, style="Percent.TLabel").pack(side=RIGHT)
        self.progress = ttk.Progressbar(card, style="Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.pack(fill=X)

        traffic = ttk.Frame(card, style="Traffic.TFrame", padding=(12, 9))
        traffic.pack(fill=X, pady=(12, 0))
        graph_box = ttk.Frame(traffic, style="Traffic.TFrame")
        graph_box.grid(row=0, column=0, rowspan=2, sticky="nsew", padx=(0, 18))
        ttk.Label(graph_box, text="TRÁFEGO DE REDE — ÚLTIMOS SEGUNDOS", style="MetricName.TLabel").pack(anchor=W)
        self.traffic_graph = TrafficGraph(graph_box)
        self.traffic_graph.pack(fill=X, expand=True, pady=(5, 0))
        metrics = (
            ("AGORA", self.current_speed_var),
            ("MÉDIA", self.average_speed_var),
            ("PICO", self.peak_speed_var),
            ("TRANSFERIDO", self.transferred_var),
            ("TEMPO RESTANTE", self.traffic_eta_var),
        )
        for column, (label, variable) in enumerate(metrics, start=1):
            metric = ttk.Frame(traffic, style="Traffic.TFrame")
            metric.grid(row=0, column=column, rowspan=2, sticky="n", padx=(8, 8))
            ttk.Label(metric, text=label, style="MetricName.TLabel").pack(anchor=W)
            ttk.Label(metric, textvariable=variable, style="MetricValue.TLabel").pack(anchor=W, pady=(5, 0))
        traffic.columnconfigure(0, weight=1)

        action = ttk.Frame(card, style="Card.TFrame")
        action.pack(fill=X, pady=(18, 0))
        self.download_button = ttk.Button(action, text="Iniciar download", style="Accent.TButton", command=self._start_download)
        self.download_button.pack(side=LEFT)
        self.cancel_button = ttk.Button(action, text="Cancelar", style="Soft.TButton", command=self._cancel, state="disabled")
        self.cancel_button.pack(side=LEFT, padx=9)
        ttk.Button(action, text="Abrir pasta", style="Soft.TButton", command=self._open_folder).pack(side=RIGHT)

        footer = ttk.Frame(self, padding=(34, 0, 34, 22))
        footer.pack(fill=X)
        ttk.Label(footer, textvariable=self.status_var, style="Status.TLabel").pack(anchor=W)

    def _update_url_count(self, _event=None):
        count = len(clean_urls(self.urls_text.get("1.0", END)))
        self.url_count_var.set(f"{count} link" if count == 1 else f"{count} links")

    def _paste_urls(self):
        try:
            content = self.clipboard_get().strip()
        except tk.TclError:
            self.status_var.set("A área de transferência está vazia.")
            return
        if content:
            current = self.urls_text.get("1.0", END).strip()
            self.urls_text.insert(END, ("\n" if current else "") + content)
            self._update_url_count()
            self.status_var.set("Links adicionados da área de transferência.")

    def _clear_urls(self):
        if self.is_downloading:
            return
        self.urls_text.delete("1.0", END)
        self._update_url_count()
        self.urls_text.focus_set()

    def _open_folder(self):
        folder = Path(self.folder_var.get().strip() or DEFAULT_FOLDER)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(folder)
        except OSError as error:
            messagebox.showerror(APP_TITLE, f"Não foi possível abrir a pasta:\n\n{error}")

    @staticmethod
    def _readable_bytes(value):
        return yt_dlp.utils.format_bytes(value or 0).replace("iB", "B")

    def _reset_traffic(self):
        self.speed_samples.clear()
        self.traffic_graph.reset()
        self.current_speed_var.set("0 B/s")
        self.average_speed_var.set("0 B/s")
        self.peak_speed_var.set("0 B/s")
        self.transferred_var.set("0 B")
        self.traffic_eta_var.set("--:--")

    def _update_traffic(self, downloaded, total, speed, eta):
        speed = float(speed or 0)
        if speed > 0:
            self.speed_samples.append(speed)
        self.traffic_graph.add(speed)
        average = sum(self.speed_samples) / len(self.speed_samples) if self.speed_samples else 0
        peak = max(self.speed_samples, default=0)
        self.current_speed_var.set(f"{self._readable_bytes(speed)}/s")
        self.average_speed_var.set(f"{self._readable_bytes(average)}/s")
        self.peak_speed_var.set(f"{self._readable_bytes(peak)}/s")
        if total:
            self.transferred_var.set(
                f"{self._readable_bytes(downloaded)} / {self._readable_bytes(total)}"
            )
        else:
            self.transferred_var.set(self._readable_bytes(downloaded))
        self.traffic_eta_var.set(format_duration(eta) if eta is not None else "calculando")

    def _choose_folder(self):
        selected = filedialog.askdirectory(initialdir=self.folder_var.get() or str(DEFAULT_FOLDER), title="Escolha a pasta de destino")
        if selected:
            self.folder_var.set(selected)

    def _start_download(self):
        urls = clean_urls(self.urls_text.get("1.0", END))
        folder = self.folder_var.get().strip()
        if not urls:
            messagebox.showwarning(APP_TITLE, "Cole pelo menos uma URL para continuar.")
            return
        if not folder:
            messagebox.showwarning(APP_TITLE, "Escolha uma pasta de destino.")
            return
        self.is_downloading = True
        self.cancel_requested.clear()
        self._reset_traffic()
        self.progress.configure(mode="indeterminate", value=0)
        self.progress.start(12)
        self.progress_is_pulsing = True
        self.percent_var.set("···")
        self.detail_var.set("Analisando os links e preparando o download...")
        self.status_var.set(f"Preparando {len(urls)} link(s)...")
        self.download_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        threading.Thread(target=self._download_worker, args=(urls, folder, self.mode_var.get()), daemon=True).start()

    def _download_worker(self, urls, folder, mode):
        last_progress_update = 0.0

        def hook(data):
            nonlocal last_progress_update
            if self.cancel_requested.is_set():
                raise DownloadCancelled()
            status = data.get("status")
            if status == "downloading":
                now = time.monotonic()
                # Dez atualizações por segundo dão movimento fluido sem lotar
                # a fila de eventos da thread gráfica.
                if now - last_progress_update < 0.1:
                    return
                last_progress_update = now

                downloaded = data.get("downloaded_bytes") or 0
                total = data.get("total_bytes") or data.get("total_bytes_estimate")
                if total:
                    percent = max(0.0, min(100.0, downloaded * 100 / total))
                else:
                    fragment = data.get("fragment_index") or 0
                    fragment_total = data.get("fragment_count") or 0
                    percent = fragment * 100 / fragment_total if fragment_total else None

                info = data.get("info_dict", {})
                title = info.get("title", "arquivo")
                speed_value = data.get("speed")
                speed = f"{yt_dlp.utils.format_bytes(speed_value)}/s" if speed_value else ""
                eta_value = data.get("eta")
                eta = format_duration(eta_value) if eta_value is not None else ""
                playlist_index = info.get("playlist_index")
                playlist_count = info.get("playlist_count")
                parts = [title]
                if playlist_index and playlist_count:
                    parts.append(f"item {playlist_index}/{playlist_count}")
                if speed:
                    parts.append(speed)
                if eta:
                    parts.append(f"restante {eta}")
                if total:
                    parts.append(
                        f"{yt_dlp.utils.format_bytes(downloaded)} de "
                        f"{yt_dlp.utils.format_bytes(total)}"
                    )
                event_name = "progress" if percent is not None else "pulse"
                self.events.put(
                    (event_name, percent, "  •  ".join(parts), downloaded, total, speed_value, eta_value)
                )
            elif status == "finished":
                self.events.put(("progress", 100, "Processando arquivo e salvando informações..."))

        try:
            logger = GuiLogger(self.events)
            exit_code = baixar_videos(urls, folder, mode, hook, logger)
            if exit_code:
                details = logger.errors[-1] if logger.errors else (
                    "O site recusou ou interrompeu o download. Tente novamente em alguns minutos."
                )
                raise DownloadFailed(details)
            self.events.put(("done", "Downloads finalizados. Os arquivos TXT foram salvos ao lado de cada mídia."))
        except DownloadCancelled:
            self.events.put(("done", "Download cancelado pelo usuário."))
        except Exception as error:
            self.events.put(("error", str(error)))

    def _cancel(self):
        self.cancel_requested.set()
        self.cancel_button.configure(state="disabled")
        self.detail_var.set("Finalizando a etapa em andamento com segurança...")
        self.status_var.set("Cancelando após a etapa atual...")

    def _process_events(self):
        try:
            # O limite mantém o loop gráfico responsivo mesmo em conexões rápidas.
            for _ in range(50):
                event = self.events.get_nowait()
                if event[0] == "progress":
                    if self.progress_is_pulsing:
                        self.progress.stop()
                        self.progress.configure(mode="determinate")
                        self.progress_is_pulsing = False
                    self.progress.configure(value=event[1])
                    self.percent_var.set(f"{event[1]:.1f}%")
                    self.detail_var.set(event[2][:115])
                    self.status_var.set("Download em andamento — você pode continuar usando a janela.")
                    if len(event) > 3:
                        self._update_traffic(*event[3:7])
                elif event[0] == "pulse":
                    if not self.progress_is_pulsing:
                        self.progress.configure(mode="indeterminate", value=0)
                        self.progress.start(12)
                        self.progress_is_pulsing = True
                    self.percent_var.set("···")
                    self.detail_var.set(event[2][:115])
                    self.status_var.set("Baixando — o servidor ainda não informou o tamanho total.")
                    self._update_traffic(*event[3:7])
                elif event[0] == "log":
                    self.status_var.set(event[1][:140])
                elif event[0] == "error":
                    self._finish(event[1], failed=True)
                elif event[0] == "done":
                    self._finish(event[1])
        except queue.Empty:
            pass
        self.after(100, self._process_events)

    def _finish(self, message, failed=False):
        self.progress.stop()
        self.progress_is_pulsing = False
        self.progress.configure(mode="determinate")
        self.is_downloading = False
        self.download_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.status_var.set(message)
        self.current_speed_var.set("0 B/s")
        self.traffic_eta_var.set("00:00:00")
        self.traffic_graph.add(0)
        cancelled = self.cancel_requested.is_set()
        self.detail_var.set("Cancelado" if cancelled else ("Concluído" if not failed else "Ocorreu uma falha"))
        if not failed and not cancelled:
            self.progress.configure(value=100)
            self.percent_var.set("100%")
        if failed:
            messagebox.showerror(APP_TITLE, f"Não foi possível concluir o download:\n\n{message}")


if __name__ == "__main__":
    DownloaderApp().mainloop()
