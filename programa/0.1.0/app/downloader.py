"""Camada de download: encapsula o yt-dlp e gerencia os jobs em segundo plano."""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads")).resolve()
# Cada download acontece numa pasta própria aqui dentro; pronto, o arquivo vai para a DOWNLOAD_DIR.
TEMP_DIR = DOWNLOAD_DIR / ".baixando"
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "2"))
HAS_FFMPEG = shutil.which("ffmpeg") is not None


class DownloadError(Exception):
    """Erro amigável para mostrar ao usuário."""


def validate_url(url: str) -> str:
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError("Informe um link válido começando com http:// ou https://")
    return url


def _base_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,  # playlists virão numa etapa futura
        "noprogress": True,
    }


def _clean_error(exc: Exception) -> str:
    msg = str(exc)
    # yt-dlp prefixa as mensagens com "ERROR: " e códigos de cor
    for prefix in ("ERROR: ", "\x1b[0;31mERROR:\x1b[0m "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    return msg


def fetch_info(url: str) -> dict:
    """Busca os metadados do vídeo sem baixar nada."""
    url = validate_url(url)
    try:
        with yt_dlp.YoutubeDL(_base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise DownloadError(_clean_error(exc)) from exc

    if info.get("_type") == "playlist":
        raise DownloadError("Esse link é uma playlist. Por enquanto só baixamos vídeos individuais.")

    heights = sorted(
        {f["height"] for f in info.get("formats") or [] if f.get("height") and f.get("vcodec") != "none"},
        reverse=True,
    )
    return {
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "platform": info.get("extractor_key"),
        "webpage_url": info.get("webpage_url") or url,
        "qualities": heights,
    }


def _format_selector(quality: str) -> str:
    if not HAS_FFMPEG:
        # Sem ffmpeg não dá para juntar vídeo e áudio separados: pega um arquivo único.
        if quality == "best":
            return "b"
        return f"b[height<={int(quality)}]/b"
    if quality == "best":
        return "bv*+ba/b"
    h = int(quality)
    return f"bv*[height<={h}]+ba/b[height<={h}]/bv*+ba/b"


@dataclass
class Job:
    id: str
    url: str
    quality: str
    status: str = "queued"  # queued | downloading | processing | finished | error
    title: str | None = None
    progress: float = 0.0  # 0-100 do arquivo atual
    stage: str = ""  # ex.: "vídeo", "áudio"
    speed: float | None = None  # bytes/s
    eta: int | None = None  # segundos
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    filename: str | None = None
    filesize: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)

    @property
    def folder(self) -> Path:
        return TEMP_DIR / self.id

    @property
    def path(self) -> Path | None:
        return DOWNLOAD_DIR / self.filename if self.filename else None

    def to_dict(self) -> dict:
        return asdict(self)


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        # Sobras de downloads interrompidos (app fechado no meio)
        shutil.rmtree(TEMP_DIR, ignore_errors=True)

    def create(self, url: str, quality: str) -> Job:
        url = validate_url(url)
        if quality != "best" and not str(quality).isdigit():
            raise DownloadError("Qualidade inválida.")
        job = Job(id=uuid.uuid4().hex[:12], url=url, quality=str(quality))
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    # ---- execução -------------------------------------------------------

    def _run(self, job: Job) -> None:
        job.folder.mkdir(parents=True, exist_ok=True)
        stream_count = {"n": 0}

        def progress_hook(d: dict) -> None:
            status = d.get("status")
            if status == "downloading":
                if job.status != "downloading":
                    job.status = "downloading"
                info = d.get("info_dict") or {}
                job.title = job.title or info.get("title")
                if info.get("vcodec") not in (None, "none"):
                    job.stage = "vídeo"
                elif info.get("acodec") not in (None, "none"):
                    job.stage = "áudio"
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes") or 0
                job.total_bytes = int(total) if total else None
                job.downloaded_bytes = int(done)
                job.progress = round(done / total * 100, 1) if total else 0.0
                job.speed = d.get("speed")
                job.eta = d.get("eta")
            elif status == "finished":
                stream_count["n"] += 1
                job.progress = 100.0
                job.speed = None
                job.eta = None

        def postprocessor_hook(d: dict) -> None:
            if d.get("status") == "started":
                job.status = "processing"
                job.stage = "finalizando"

        opts = {
            **_base_opts(),
            "format": _format_selector(job.quality),
            "outtmpl": str(job.folder / "%(title).150B [%(id)s].%(ext)s"),
            "restrictfilenames": False,
            "windowsfilenames": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
        }
        if HAS_FFMPEG:
            opts["merge_output_format"] = "mp4"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(job.url, download=True)
                job.title = info.get("title") or job.title
            final = self._find_output(job.folder)
            if final is None:
                raise DownloadError("O download terminou, mas o arquivo não foi encontrado.")
            destino = self._move_to_downloads(final)
            job.filename = destino.name
            job.filesize = destino.stat().st_size
            job.progress = 100.0
            job.stage = ""
            job.status = "finished"
        except yt_dlp.utils.DownloadError as exc:
            job.status = "error"
            job.error = _clean_error(exc)
        except Exception as exc:  # noqa: BLE001 - mostrar qualquer falha ao usuário
            job.status = "error"
            job.error = str(exc) or exc.__class__.__name__
        finally:
            shutil.rmtree(job.folder, ignore_errors=True)
            try:
                TEMP_DIR.rmdir()  # some quando não há outro download em andamento
            except OSError:
                pass

    _move_lock = threading.Lock()

    def _move_to_downloads(self, src: Path) -> Path:
        """Move para a pasta de downloads sem sobrescrever: "Nome (2).mp4" se já existir."""
        with self._move_lock:
            dest = DOWNLOAD_DIR / src.name
            n = 2
            while dest.exists():
                dest = DOWNLOAD_DIR / f"{src.stem} ({n}){src.suffix}"
                n += 1
            shutil.move(str(src), dest)
            return dest

    @staticmethod
    def _find_output(folder: Path) -> Path | None:
        candidates = [
            p for p in folder.iterdir()
            if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp"))
        ]
        return max(candidates, key=lambda p: p.stat().st_size, default=None)


manager = JobManager()
