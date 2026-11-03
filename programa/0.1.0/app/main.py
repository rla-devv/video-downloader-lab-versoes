"""API web do Video Downloader Lab."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .downloader import DOWNLOAD_DIR, HAS_FFMPEG, DownloadError, fetch_info, manager

STATIC_DIR = Path(__file__).parent / "static"
# App instalado no Windows (downloaderlab.pyw): o arquivo já fica na pasta Downloads e dá para mostrá-lo no Explorador.
DESKTOP = os.environ.get("VDL_DESKTOP") == "1"
# Só atende pedidos endereçados a este computador (evita que outro site use o app pelo navegador).
HOSTS_LOCAIS = {"127.0.0.1", "localhost", "::1"}
HOSTS_EXTRAS = {h.strip().lower() for h in os.environ.get("VDL_ALLOWED_HOSTS", "").split(",") if h.strip()}

app = FastAPI(title="Video Downloader Lab", version=__version__, docs_url=None, redoc_url=None)


def _nome_do_host(valor: str) -> str:
    try:
        return (urlsplit("//" + valor).hostname or "").lower()
    except ValueError:
        return ""


def _host_permitido(nome: str) -> bool:
    return nome in HOSTS_LOCAIS or nome in HOSTS_EXTRAS


@app.middleware("http")
async def so_este_computador(request: Request, call_next):
    host = request.headers.get("host", "")
    if not _host_permitido(_nome_do_host(host)):
        return JSONResponse({"detail": "Acesso não permitido."}, status_code=403)
    origem = request.headers.get("origin")
    if request.method not in ("GET", "HEAD") and origem is not None:
        partes = urlsplit(origem)
        if partes.netloc.lower() != host.lower():
            return JSONResponse({"detail": "Acesso não permitido."}, status_code=403)
    resposta = await call_next(request)
    resposta.headers["X-Frame-Options"] = "DENY"
    resposta.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
    resposta.headers["X-Content-Type-Options"] = "nosniff"
    return resposta


class InfoRequest(BaseModel):
    url: str


class DownloadRequest(BaseModel):
    url: str
    quality: str = "best"


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": __version__, "ffmpeg": HAS_FFMPEG, "desktop": DESKTOP,
            "download_dir": str(DOWNLOAD_DIR) if DESKTOP else None}


@app.post("/api/info")
async def info(req: InfoRequest) -> dict:
    try:
        return await run_in_threadpool(fetch_info, req.url)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/downloads", status_code=201)
def create_download(req: DownloadRequest) -> dict:
    try:
        job = manager.create(req.url, req.quality)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.to_dict()


@app.get("/api/downloads")
def list_downloads() -> list[dict]:
    return [job.to_dict() for job in manager.all()]


@app.get("/api/downloads/{job_id}")
def get_download(job_id: str) -> dict:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Download não encontrado.")
    return job.to_dict()


def _arquivo_pronto(job_id: str) -> Path:
    job = manager.get(job_id)
    if job is None or job.status != "finished" or job.path is None:
        raise HTTPException(status_code=404, detail="Arquivo ainda não disponível.")
    path = job.path.resolve()
    if path.parent != DOWNLOAD_DIR or not path.is_file():
        raise HTTPException(status_code=404, detail="O arquivo não está mais na pasta (foi movido ou apagado).")
    return path


@app.get("/api/downloads/{job_id}/file")
def get_file(job_id: str) -> FileResponse:
    path = _arquivo_pronto(job_id)
    return FileResponse(path, filename=path.name)


@app.post("/api/downloads/{job_id}/mostrar")
def mostrar_na_pasta(job_id: str) -> dict:
    if not DESKTOP or os.name != "nt":
        raise HTTPException(status_code=404, detail="Disponível só no app instalado.")
    path = _arquivo_pronto(job_id)
    # Caminho completo: só o nome faria o Windows procurar antes na pasta atual.
    explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
    subprocess.Popen([str(explorer), "/select,", str(path)])
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
