"""API web do Video Downloader Lab."""

from __future__ import annotations

import json
import os
import secrets
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__
from .downloader import DOWNLOAD_DIR, HAS_FFMPEG, MAX_PLAYLIST, DownloadError, fetch_info, manager, validar_opcoes

STATIC_DIR = Path(__file__).parent / "static"
# App instalado no Windows (downloaderlab.pyw): o arquivo já fica na pasta Downloads e dá para mostrá-lo no Explorador.
DESKTOP = os.environ.get("VDL_DESKTOP") == "1"
# Só atende pedidos endereçados a este computador (evita que outro site use o app pelo navegador).
HOSTS_LOCAIS = {"127.0.0.1", "localhost", "::1"}
HOSTS_EXTRAS = {h.strip().lower() for h in os.environ.get("VDL_ALLOWED_HOSTS", "").split(",") if h.strip()}

# Atualização pelo botão (só no app instalado): o downloaderlab.pyw preenche com as funções dele.
ATUALIZADOR: dict | None = None
TOKEN = os.environ.get("VDL_TOKEN", "")

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
    opcoes: dict = {}
    referer: str | None = None
    title: str | None = None
    thumbnail: str | None = None


class ItemLote(BaseModel):
    url: str
    title: str | None = None
    thumbnail: str | None = None


class LoteRequest(BaseModel):
    """Vários vídeos de uma playlist ou canal, com as mesmas opções, numa subpasta com o nome da playlist."""
    itens: list[ItemLote]
    opcoes: dict = {}
    pasta: str | None = None


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
        job = manager.create(req.url, req.opcoes, req.referer, req.title, req.thumbnail)
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return job.to_dict()


@app.post("/api/downloads/lote", status_code=201)
def create_batch(req: LoteRequest) -> dict:
    if not req.itens or len(req.itens) > MAX_PLAYLIST:
        raise HTTPException(status_code=400, detail=f"Escolha de 1 a {MAX_PLAYLIST} vídeos.")
    try:
        validar_opcoes(req.opcoes)   # confere antes de criar o primeiro
        jobs = [manager.create(i.url, req.opcoes, None, i.title, i.thumbnail, req.pasta) for i in req.itens]
    except DownloadError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"criados": len(jobs)}


@app.post("/api/downloads/{job_id}/cancelar")
def cancel_download(job_id: str) -> dict:
    job = manager.cancel(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Download não encontrado.")
    return job.to_dict()


@app.post("/api/downloads/cancelar-fila")
def cancel_queue() -> dict:
    n = 0
    for job in manager.all():
        if job.status == "queued":
            manager.cancel(job.id)
            n += 1
    return {"cancelados": n}


@app.post("/api/downloads/limpar")
def clear_finished() -> dict:
    """Tira da lista os prontos, com erro e cancelados (os arquivos continuam na pasta)."""
    return {"removidos": manager.remover_concluidos()}


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
    if not path.is_relative_to(DOWNLOAD_DIR) or not path.is_file():
        raise HTTPException(status_code=404, detail="O arquivo não está mais na pasta (foi movido ou apagado).")
    return path


@app.get("/api/downloads/{job_id}/file")
def get_file(job_id: str) -> FileResponse:
    path = _arquivo_pronto(job_id)
    return FileResponse(path, filename=path.name)


def _explorer(*args: str) -> None:
    # Caminho completo: só o nome faria o Windows procurar antes na pasta atual.
    explorer = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "explorer.exe"
    subprocess.Popen([str(explorer), *args])


@app.post("/api/pasta")
def abrir_pasta() -> dict:
    if not DESKTOP or os.name != "nt":
        raise HTTPException(status_code=404, detail="Disponível só no app instalado.")
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    _explorer(str(DOWNLOAD_DIR))
    return {"ok": True}


@app.post("/api/downloads/{job_id}/mostrar")
def mostrar_na_pasta(job_id: str) -> dict:
    if not DESKTOP or os.name != "nt":
        raise HTTPException(status_code=404, detail="Disponível só no app instalado.")
    path = _arquivo_pronto(job_id)
    _explorer("/select,", str(path))
    return {"ok": True}


# ---------- atualização (app instalado) ----------

def _token_ok(request: Request) -> bool:
    enviado = request.headers.get("x-vdl-token", "")
    return bool(TOKEN) and secrets.compare_digest(enviado.encode("utf-8", "replace"), TOKEN.encode())


def _atualizador(request: Request) -> dict:
    if ATUALIZADOR is None or not _token_ok(request):
        raise HTTPException(status_code=403, detail="Sem permissão.")
    return ATUALIZADOR


@app.get("/api/atualizacao")
async def consultar_atualizacao(request: Request, forcar: int = 0) -> dict:
    atualizador = _atualizador(request)
    return await run_in_threadpool(atualizador["verificar"], bool(forcar))


async def _trocar_versao(request: Request, acao: str) -> dict:
    atualizador = _atualizador(request)
    if manager.ocupado():
        return {"ok": False, "erro": "há um download em andamento; espere ele terminar"}
    try:
        return {"ok": True, **await run_in_threadpool(atualizador[acao])}
    except Exception as exc:  # noqa: BLE001 - a página mostra o motivo; nada foi trocado
        return {"ok": False, "erro": str(exc) or exc.__class__.__name__}


@app.post("/api/atualizacao")
async def instalar_atualizacao(request: Request) -> dict:
    return await _trocar_versao(request, "instalar")


@app.post("/api/atualizacao/restaurar")
async def voltar_versao(request: Request) -> dict:
    return await _trocar_versao(request, "restaurar")


@app.get("/", include_in_schema=False)
@app.get("/index.html", include_in_schema=False)
def pagina() -> HTMLResponse:
    """A página; no app instalado, com a marca que a identifica (e o token da atualização)."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    if DESKTOP:
        marca = f"<script>window.__DOWNLOADERLAB_APP = {json.dumps({'token': TOKEN, 'api': 1})};</script>\n"
        html = html.replace("<head>\n", "<head>\n" + marca, 1)
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
