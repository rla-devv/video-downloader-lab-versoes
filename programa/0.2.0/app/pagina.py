"""Procura o vídeo dentro de uma página que o yt-dlp não reconhece (ex.: página de curso com player do Vimeo embutido).

1. Lê o HTML da página e procura players (iframe, embed, video, source) que algum extrator do yt-dlp conheça.
2. Não achou: abre a página no Edge (ou Chrome) sem janela, deixa o JavaScript rodar e lê de novo. Muitos sites
   montam o player só depois de carregar. O modo --headless=new já usa um perfil temporário próprio: não mexe no
   perfil da pessoa nem briga com o Edge aberto. (Não passe --user-data-dir: com ele o navegador não termina.)
"""

from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin, urlparse

import yt_dlp

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
TEMPO_NAVEGADOR = int(os.environ.get("VDL_TEMPO_NAVEGADOR", "45"))   # segundos, no máximo
# Endereços que aparecem em toda página e nunca são o vídeo.
IGNORAR = re.compile(r"googletagmanager|google-analytics|doubleclick|facebook\.com/tr|hotjar|clarity\.ms|"
                     r"recaptcha|/cdn-cgi/|about:blank", re.I)
TAG_COM_SRC = re.compile(r"<(?:iframe|embed|video|source)\b[^>]*?\s(?:data-)?src\s*=\s*[\"']([^\"']+)[\"']", re.I)
ARQUIVO_DE_VIDEO = re.compile(r"\.(mp4|m3u8|mpd|webm|mov)(\?|$)", re.I)


@lru_cache(maxsize=1)
def _extratores():
    return [e for e in yt_dlp.extractor.gen_extractor_classes() if e.ie_key() != "Generic"]


def _conhecido(url: str) -> bool:
    return any(e.suitable(url) for e in _extratores())


def _candidatos(texto: str, base: str) -> list[str]:
    """Players que o yt-dlp conhece primeiro; arquivos de vídeo diretos depois. Sem repetidos, na ordem da página."""
    players, arquivos = [], []
    for bruto in TAG_COM_SRC.findall(texto):
        url = urljoin(base, html.unescape(bruto).strip())
        if urlparse(url).scheme not in ("http", "https") or IGNORAR.search(url):
            continue
        if _conhecido(url):
            players.append(url)
        elif ARQUIVO_DE_VIDEO.search(url):
            arquivos.append(url)
    return list(dict.fromkeys(players + arquivos))


def _navegador() -> str | None:
    """Edge (vem no Windows 10 e 11) ou Chrome. VDL_NAVEGADOR escolhe outro (testes)."""
    if os.environ.get("VDL_NAVEGADOR"):
        return os.environ["VDL_NAVEGADOR"]
    if os.name == "nt":
        pastas = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        relativos = [r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe"]
        for rel in relativos:
            for pasta in pastas:
                if pasta and (Path(pasta) / rel).is_file():
                    return str(Path(pasta) / rel)
        return None
    for nome in ("microsoft-edge", "google-chrome", "chromium", "chromium-browser"):
        if shutil.which(nome):
            return shutil.which(nome)
    return None


def _html_depois_do_javascript(url: str) -> str | None:
    exe = _navegador()
    if not exe:
        return None
    args = [exe, "--headless=new", "--disable-gpu", "--mute-audio", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", "--disable-sync", "--disable-background-networking", "--virtual-time-budget=15000",
            "--dump-dom", url]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        args.insert(1, "--no-sandbox")   # só no Linux como root (testes); no Windows nunca
    try:
        r = subprocess.run(args, capture_output=True, timeout=TEMPO_NAVEGADOR, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode("utf-8", "replace") or None


def videos_na_pagina(url: str) -> list[str]:
    """Endereços de vídeo encontrados na página, do mais provável ao menos provável."""
    try:
        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            with ydl.urlopen(url) as r:
                texto = r.read(5_000_000).decode("utf-8", "replace")
        achados = _candidatos(texto, url)
    except Exception:
        achados = []
    if not achados:
        texto = _html_depois_do_javascript(url)
        if texto:
            achados = _candidatos(texto, url)
    return achados
