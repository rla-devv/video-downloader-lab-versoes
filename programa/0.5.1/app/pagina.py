"""Procura o vídeo dentro de uma página que o yt-dlp não reconhece (ex.: página de curso com player do Vimeo embutido).

1. Lê o HTML da página e procura players (iframe, embed, video, source) que algum extrator do yt-dlp conheça.
2. Não achou: abre a página no Edge (ou Chrome) sem janela, deixa o JavaScript rodar e lê de novo. Muitos sites
   montam o player só depois de carregar. No Windows, primeiro com um perfil temporário próprio (sem ele, com o Edge
   da pessoa aberto, o pedido vai para o Edge dela e volta vazio). Cada tentativa vai para o log.
"""

from __future__ import annotations

import html
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yt_dlp

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TEMPO_NAVEGADOR = int(os.environ.get("VDL_TEMPO_NAVEGADOR", "30"))   # segundos, no máximo, por tentativa
log = logging.getLogger("downloaderlab.pagina")
# Endereços que aparecem em toda página e nunca são o vídeo.
IGNORAR = re.compile(r"googletagmanager|google-analytics|doubleclick|facebook\.com/tr|hotjar|clarity\.ms|"
                     r"recaptcha|/cdn-cgi/|about:blank", re.I)
# Leitura em passada única (tempo linear): acha o início de cada tag, pula para o ">" seguinte e só procura o src
# dentro dela. Um regex só, sobre a página toda, levava horas numa página com "<iframe " repetido sem ">".
INICIO_TAG = re.compile(r"<(?:iframe|embed|video|source)\b", re.I)
SRC = re.compile(r"\s(?:data-)?src\s*=\s*[\"']([^\"']{1,4000})[\"']", re.I)
MAX_TAG = 6000


def _srcs(texto: str):
    fim = -1
    for m in INICIO_TAG.finditer(texto):
        if fim < m.end():
            fim = texto.find(">", m.end())
            if fim < 0:
                return
        if fim - m.end() > MAX_TAG:   # tag malformada ou gigante: ignora
            continue
        achado = SRC.search(texto, m.end(), fim)
        if achado:
            yield achado.group(1)


MAX_CANDIDATOS = 20
ARQUIVO_DE_VIDEO = re.compile(r"\.(mp4|m3u8|mpd|webm|mov)(\?|$)", re.I)


@lru_cache(maxsize=1)
def _extratores():
    return [e for e in yt_dlp.extractor.gen_extractor_classes() if e.ie_key() != "Generic"]


def _conhecido(url: str) -> bool:
    return any(e.suitable(url) for e in _extratores())


def _candidatos(texto: str, base: str) -> list[str]:
    """Players que o yt-dlp conhece primeiro; arquivos de vídeo diretos depois. Sem repetidos, na ordem da página."""
    players, arquivos = [], []
    for n, bruto in enumerate(_srcs(texto)):
        if n >= 500:   # uma página de verdade não tem tantos players
            break
        url = urljoin(base, html.unescape(bruto).strip())
        if urlparse(url).scheme not in ("http", "https") or IGNORAR.search(url):
            continue
        if _conhecido(url):
            players.append(url)
        elif ARQUIVO_DE_VIDEO.search(url):
            arquivos.append(url)
    return list(dict.fromkeys(players + arquivos))[:MAX_CANDIDATOS]


def _navegadores() -> list[str]:
    """Edge (vem no Windows 10 e 11) e Chrome, os que existirem. VDL_NAVEGADOR escolhe outro (testes)."""
    if os.environ.get("VDL_NAVEGADOR"):
        return [os.environ["VDL_NAVEGADOR"]]
    achados = []
    if os.name == "nt":
        pastas = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"):
            for pasta in pastas:
                if pasta and (Path(pasta) / rel).is_file():
                    achados.append(str(Path(pasta) / rel))
                    break
        return achados
    for nome in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(nome):
            achados.append(shutil.which(nome))
    return achados


def _encerrar(proc: subprocess.Popen) -> None:
    """Tempo esgotado: fecha o navegador e os processos filhos dele (no Windows, o kill sozinho deixa os filhos)."""
    try:
        if os.name == "nt":
            taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(taskkill), "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=15,
                           creationflags=NO_WINDOW)
        proc.kill()
        proc.communicate(timeout=10)
    except Exception:  # noqa: BLE001 - já estamos desistindo desta tentativa
        pass


def _rodar(exe: str, url: str, perfil: str | None) -> tuple[str, str]:
    """Abre a página sem janela e devolve (HTML depois do JavaScript, motivo se falhou)."""
    args = [exe, "--headless=new", "--disable-gpu", "--mute-audio", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking", "--virtual-time-budget=15000"]
    if perfil:
        args.append(f"--user-data-dir={perfil}")
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")   # só no Linux como root (testes); no Windows nunca
    args += ["--dump-dom", url]
    inicio = time.monotonic()
    try:
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=NO_WINDOW)
    except OSError as exc:
        return "", f"não abriu ({exc})"
    try:
        saida, erros = proc.communicate(timeout=TEMPO_NAVEGADOR)
    except subprocess.TimeoutExpired:
        _encerrar(proc)
        return "", f"tempo esgotado ({TEMPO_NAVEGADOR} s)"
    texto = saida.decode("utf-8", "replace")
    duracao = time.monotonic() - inicio
    if not texto.strip():
        fim = erros.decode("utf-8", "replace").strip().splitlines()[-3:]
        return "", f"devolveu a página vazia (código {proc.returncode}, {duracao:.1f} s){': ' + ' | '.join(fim) if fim else ''}"
    log.info("página aberta em %.1f s (%d caracteres)", duracao, len(texto))
    return texto, ""


def _html_depois_do_javascript(url: str) -> list[str]:
    """Tenta cada navegador (Edge, depois Chrome), sempre com um perfil temporário próprio no Windows."""
    exes = _navegadores()
    if not exes:
        log.warning("procurar o vídeo na página: não achei o Microsoft Edge nem o Google Chrome")
        return []
    for exe in exes:
        # No Windows, sempre com perfil temporário: sem ele, a página iria para o Edge/Chrome da pessoa (com as
        # contas dela), escondida. No Linux dos testes, só sem perfil (com ele, o Chromium daqui não termina).
        for com_perfil in ((True,) if os.name == "nt" else (False,)):
            perfil = tempfile.mkdtemp(prefix="downloaderlab-pagina-") if com_perfil else None
            try:
                texto, motivo = _rodar(exe, url, perfil)
            finally:
                if perfil:
                    shutil.rmtree(perfil, ignore_errors=True)
            jeito = f"{Path(exe).name} {'com perfil próprio' if com_perfil else 'sem perfil'}"
            if motivo:
                log.warning("procurar o vídeo na página (%s): %s", jeito, motivo)
                continue
            achados = _candidatos(texto, url)
            log.info("procurar o vídeo na página (%s): %d player(s) encontrado(s)", jeito, len(achados))
            if achados:
                return achados
    return []


def videos_na_pagina(url: str) -> list[str]:
    """Endereços de vídeo encontrados na página, do mais provável ao menos provável."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            with ydl.urlopen(url) as r:
                texto = r.read(5_000_000).decode("utf-8", "replace")
        achados = _candidatos(texto, url)
    except Exception as exc:  # noqa: BLE001 - segue para o navegador
        log.info("procurar o vídeo na página: não consegui ler o HTML direto (%s)", exc)
        achados = []
    if achados:
        log.info("procurar o vídeo na página: %d player(s) no HTML", len(achados))
        return achados
    return _html_depois_do_javascript(url)
