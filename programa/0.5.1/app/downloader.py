"""Camada de download: encapsula o yt-dlp e gerencia os jobs em segundo plano."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import logging

import yt_dlp

from .pagina import videos_na_pagina

log = logging.getLogger("downloaderlab.download")

DOWNLOAD_DIR = Path(os.environ.get("DOWNLOAD_DIR", "downloads")).resolve()
# Cada download acontece numa pasta própria aqui dentro; pronto, o arquivo vai para a DOWNLOAD_DIR.
TEMP_DIR = DOWNLOAD_DIR / ".baixando"
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "2"))
FFMPEG_BIN = shutil.which("ffmpeg")   # caminho completo (no app instalado, a pasta ffmpeg/ vem primeiro no PATH)
HAS_FFMPEG = FFMPEG_BIN is not None
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_PLAYLIST = 1000          # itens listados de uma playlist ou canal
EXT_LEGENDA = (".srt", ".vtt", ".ass", ".ttml", ".srv3", ".json3")
ATIVOS = ("queued", "downloading", "processing")
MAX_LINKS = 200              # links colados de uma vez
# Login da pessoa, só se ela escolher: os cookies do navegador (o yt-dlp lê do próprio navegador) ou um cookies.txt
# exportado por ela. Ficam neste computador e vão só para o site do vídeo.
CONTAS = ("nenhuma", "firefox", "edge", "chrome", "brave", "opera", "arquivo")
NOMES_NAVEGADOR = {"firefox": "Firefox", "edge": "Microsoft Edge", "chrome": "Google Chrome", "brave": "Brave",
                   "opera": "Opera"}
DADOS_DIR = Path(os.environ.get("VDL_DADOS", "dados")).resolve()
COOKIES_ARQ = DADOS_DIR / "cookies.txt"
# O yt-dlp regrava o arquivo de cookies ao terminar (com os cookies de todo site que visitou). Cada consulta usa uma
# cópia temporária: o original da pessoa nunca é reescrito nem corrompido por downloads ao mesmo tempo.
COPIAS_COOKIES = DADOS_DIR / "cookies-em-uso"


class DownloadError(Exception):
    """Erro amigável para mostrar ao usuário."""


class Cancelado(yt_dlp.utils.DownloadCancelled):
    """A pessoa cancelou o download. É um DownloadCancelled: o yt-dlp para na hora, inclusive quando baixa vários
    pedaços ao mesmo tempo (uma exceção comum ali seria tratada como falha de um pedaço e tentada de novo)."""
    msg = "Download cancelado"


def validate_url(url: str) -> str:
    url = (url or "").strip()
    # Sem espaço nem caracteres de controle no meio: um "\r\n" num Referer viraria cabeçalho extra no pedido ao site.
    if any(ord(c) < 0x21 or ord(c) == 0x7F for c in url) or len(url) > 4000:
        raise DownloadError("Esse link tem caracteres inválidos. Copie o endereço de novo.")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DownloadError("Informe um link válido começando com http:// ou https://")
    return url


def _base_opts(referer: str | None = None, conta: str = "nenhuma") -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,      # link de vídeo dentro de uma playlist: só o vídeo
        "noprogress": True,
    }
    if referer:
        # Players embutidos (ex.: Vimeo restrito a um site) só entregam o vídeo para a página onde estão.
        opts["http_headers"] = {"Referer": referer}
    if conta in NOMES_NAVEGADOR:
        opts["cookiesfrombrowser"] = (conta,)
    elif conta == "arquivo":
        if not COOKIES_ARQ.is_file():
            raise DownloadError("O arquivo cookies.txt não está no app. Escolha o arquivo de novo em \"Entrar com a "
                                "sua conta\".")
        COPIAS_COOKIES.mkdir(parents=True, exist_ok=True)
        copia = COPIAS_COOKIES / f"{uuid.uuid4().hex}.txt"
        shutil.copyfile(COOKIES_ARQ, copia)
        opts["cookiefile"] = str(copia)
    return opts


def _soltar(opts: dict) -> None:
    """Apaga a cópia temporária dos cookies, se esta consulta usou uma."""
    arq = opts.get("cookiefile")
    if arq and Path(arq).parent == COPIAS_COOKIES:
        Path(arq).unlink(missing_ok=True)


def _clean_error(exc: Exception) -> str:
    msg = str(exc)
    # yt-dlp prefixa as mensagens com "ERROR: " e códigos de cor
    for prefix in ("ERROR: ", "\x1b[0;31mERROR:\x1b[0m "):
        if msg.startswith(prefix):
            msg = msg[len(prefix):]
    return msg


def _meta(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("instagram.com"):
        return "Instagram"
    if host.endswith(("facebook.com", "fb.watch", "fb.com")):
        return "Facebook"
    return None


def _amigavel(msg: str, url: str = "", conta: str = "nenhuma") -> str:
    """As falhas mais comuns em português, dizendo o que fazer."""
    nav = NOMES_NAVEGADOR.get(conta, "navegador")
    baixo = msg.lower()
    if "could not copy chrome cookie database" in baixo:
        return (f"Não consegui ler o login do {nav}: com ele aberto, o arquivo fica travado. Feche o {nav} por completo "
                "e tente de novo, ou use o Firefox ou um arquivo cookies.txt.")
    if "failed to decrypt" in baixo or "dpapi" in baixo or "app-bound" in baixo:
        return (f"O {nav} protege o login de um jeito que outros programas não conseguem ler. Use o Firefox (entre na "
                "conta nele) ou um arquivo cookies.txt.")
    if "could not find" in baixo and "cookies database" in baixo:
        return f"Não achei o login do {nav} neste computador. Confira se é esse o navegador em que você entrou na conta."
    site = _meta(url)
    if site and any(t in baixo for t in ("login", "log in", "rate-limit", "rate limit", "not available",
                                         "cookies", "private", "checkpoint", "authentication")):
        if conta == "nenhuma":
            return (f"O {site} pediu login para mostrar esse vídeo. Abra \"Entrar com a sua conta\", escolha o navegador "
                    f"em que você está logado no {site} e analise de novo.")
        return (f"O {site} recusou mesmo com o login. Confira se você consegue ver o vídeo no navegador escolhido; "
                "se o vídeo for privado, só quem tem acesso consegue baixar.")
    return msg


# Perfil do Instagram (instagram.com/<nome>/): o extrator de perfil do yt-dlp está quebrado (o Instagram bloqueia).
VIDEO_FACEBOOK = re.compile(r"/(videos?|reels?|watch|share|v)/|video\.php|story\.php|fb\.watch|[?&]v=", re.I)
PERFIL_INSTAGRAM = re.compile(r"^https?://(?:www\.)?instagram\.com/(?!p/|reels?/|tv/|stories/|explore/)[^/?#]{2,}/?(?:[?#].*)?$", re.I)


def _nao_reconhecido(exc: yt_dlp.utils.DownloadError) -> bool:
    causa = (exc.exc_info or (None, None))[1]
    return isinstance(causa, yt_dlp.utils.UnsupportedError) or "Unsupported URL" in str(exc)


def _extrair(url: str, referer: str | None = None, conta: str = "nenhuma") -> dict:
    # Playlists e canais vêm "rasos" (só a lista, sem abrir cada vídeo): rápido mesmo com centenas de itens.
    opts = {**_base_opts(referer, conta), "extract_flat": "in_playlist", "playlistend": MAX_PLAYLIST}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=False)
    finally:
        _soltar(opts)


# ---------- análise ----------

def _legendas(info: dict) -> list[dict]:
    """Idiomas de legenda: as feitas pelo canal e, das automáticas, só as do idioma original e as mais comuns."""
    vistos, saida = set(), []
    for codigo, faixas in sorted((info.get("subtitles") or {}).items()):
        if codigo == "live_chat" or codigo in vistos:
            continue
        vistos.add(codigo)
        nome = next((f.get("name") for f in faixas or [] if f.get("name")), None)
        saida.append({"code": codigo, "name": nome or codigo, "auto": False})
    comuns = {"pt", "pt-BR", "en", "es"}
    for codigo, faixas in sorted((info.get("automatic_captions") or {}).items()):
        if codigo in vistos or not (codigo.endswith("-orig") or codigo in comuns):
            continue
        vistos.add(codigo)
        nome = next((f.get("name") for f in faixas or [] if f.get("name")), None)
        saida.append({"code": codigo, "name": nome or codigo, "auto": True})
    return saida


def _site(info: dict) -> str | None:
    """Nome do site para mostrar: "PeerTubePlaylist", "YoutubeTab" → "PeerTube", "Youtube"."""
    nome = info.get("extractor_key") or ""
    return re.sub(r"(Playlist|Tab|User|Channel|Search|Album|Live|Clip)+$", "", nome) or nome or None


def _miniatura(entrada: dict) -> str | None:
    if entrada.get("thumbnail"):
        return entrada["thumbnail"]
    miniaturas = [t for t in entrada.get("thumbnails") or [] if t.get("url")]
    return miniaturas[-1]["url"] if miniaturas else None


def _playlist(info: dict, url: str, conta: str = "nenhuma") -> dict:
    entradas = list(info.get("entries") or [])
    # Canal do YouTube: a primeira lista são as abas (Vídeos, Shorts, Ao vivo). Abre a de vídeos.
    if entradas and all(e.get("_type") == "playlist" or e.get("ie_key") == "YoutubeTab" for e in entradas):
        aba = next((e for e in entradas if str(e.get("url", "")).rstrip("/").endswith("/videos")), entradas[0])
        if aba.get("url"):
            info = _extrair(aba["url"], conta=conta)
            entradas = list(info.get("entries") or [])
    itens = []
    for e in entradas:
        link = e.get("url") or e.get("webpage_url")
        if not link or urlparse(link).scheme not in ("http", "https"):
            continue
        itens.append({"url": link, "title": e.get("title") or link, "duration": e.get("duration"),
                      "thumbnail": _miniatura(e)})
    if not itens:
        raise DownloadError("Essa playlist está vazia ou é privada.")
    return {
        "kind": "playlist",
        "title": info.get("title") or "Playlist",
        "uploader": info.get("uploader") or info.get("channel"),
        "platform": _site(info),
        "webpage_url": info.get("webpage_url") or url,
        "count": len(itens),
        "truncated": len(itens) >= MAX_PLAYLIST,
        "entries": itens,
    }


def fetch_info(url: str, conta: str = "nenhuma") -> dict:
    """Busca os metadados sem baixar nada. Link que o yt-dlp não reconhece: procura o vídeo dentro da página."""
    url = validate_url(url)
    if conta not in CONTAS:
        raise DownloadError("Escolha de conta inválida.")
    if PERFIL_INSTAGRAM.match(url):
        raise DownloadError("Perfis inteiros do Instagram não dá para baixar: o Instagram bloqueia esse acesso. Abra os "
                            "posts ou reels que você quer e copie o link de cada um; dá para colar vários de uma vez, um "
                            "por linha.")
    origem, referer, na_pagina = url, None, False
    try:
        info = _extrair(url, conta=conta)
    except yt_dlp.utils.DownloadError as exc:
        if _meta(url) == "Facebook" and not VIDEO_FACEBOOK.search(url):
            raise DownloadError("Perfis e páginas inteiras do Facebook não dá para baixar. Abra o vídeo ou reel que você "
                                "quer e copie o link dele; dá para colar vários de uma vez, um por linha.") from exc
        if not _nao_reconhecido(exc):
            raise DownloadError(_amigavel(_clean_error(exc), url, conta)) from exc
        info = None
        ultimo_erro = None
        for candidato in videos_na_pagina(url):
            try:
                info = _extrair(candidato, referer=url, conta=conta)
                origem, referer, na_pagina = candidato, url, True
                break
            except yt_dlp.utils.DownloadError as exc2:
                ultimo_erro = exc2
        if info is None:
            if ultimo_erro is not None:
                raise DownloadError("Achei um vídeo na página, mas não consegui abri-lo: "
                                    + _clean_error(ultimo_erro)) from ultimo_erro
            detalhe = (" Os detalhes ficaram em logs\\downloaderlab.log, na pasta do app."
                       if os.environ.get("VDL_DESKTOP") == "1" else "")
            raise DownloadError("Não encontrei nenhum vídeo nesse link. Confira se o vídeo aparece na página "
                                "sem precisar entrar com login." + detalhe) from exc

    if info.get("_type") == "playlist":
        return _playlist(info, url, conta)

    formatos = info.get("formats") or []
    heights = sorted({f["height"] for f in formatos if f.get("height") and f.get("vcodec") != "none"}, reverse=True)
    so_audio = bool(formatos) and all(f.get("vcodec") == "none" for f in formatos if f.get("vcodec"))
    return {
        "kind": "video",
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail"),
        "platform": _site(info),
        "webpage_url": origem if na_pagina else (info.get("webpage_url") or url),
        "referer": referer,
        "found_in_page": na_pagina,
        "qualities": heights,
        "audio_only": so_audio,
        "subtitles": _legendas(info),
    }


def fetch_lista(urls: list[str], conta: str = "nenhuma") -> dict:
    """Vários links colados de uma vez: analisa até 4 ao mesmo tempo. Playlist no meio vira os vídeos dela; link com
    erro aparece na lista com o motivo (e não entra no download)."""
    vistos, limpos = set(), []
    for u in urls:
        u = (u or "").strip()
        if u and u not in vistos and urlparse(u).scheme in ("http", "https"):
            vistos.add(u)
            limpos.append(u)
    if not limpos:
        raise DownloadError("Não achei nenhum link (cada um começa com http:// ou https://).")
    if len(limpos) > MAX_LINKS:
        raise DownloadError(f"São {len(limpos)} links: cole até {MAX_LINKS} de uma vez.")

    def um(u):
        try:
            return u, fetch_info(u, conta), None
        except DownloadError as exc:
            return u, None, str(exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        resultados = list(pool.map(um, limpos))
    motivos = {erro for _, _, erro in resultados}
    if len(motivos) == 1 and None not in motivos:   # todos falharam pelo mesmo motivo (ex.: login): uma mensagem só
        raise DownloadError(motivos.pop())
    itens = []
    for u, info, erro in resultados:
        if erro:
            itens.append({"url": u, "title": u, "erro": erro})
        elif info["kind"] == "playlist":
            itens.extend(info["entries"])
        else:
            itens.append({"url": info["webpage_url"], "referer": info.get("referer"), "title": info.get("title") or u,
                          "duration": info.get("duration"), "thumbnail": info.get("thumbnail")})
    itens = itens[:MAX_PLAYLIST]
    erros = sum(1 for i in itens if i.get("erro"))
    return {"kind": "lista", "title": f"{len(limpos)} links colados", "count": len(itens), "erros": erros,
            "truncated": False, "entries": itens}


def salvar_cookies(texto: str) -> None:
    """Guarda o cookies.txt (formato Netscape, o que as extensões de navegador exportam) só neste computador."""
    linhas = [l for l in texto.splitlines() if l.strip() and not l.startswith("#")]
    if len(texto) > 2_000_000 or not linhas or not all(len(l.split("\t")) >= 7 for l in linhas[:50]):
        raise DownloadError("Esse arquivo não parece um cookies.txt (formato Netscape). Exporte de novo pela extensão.")
    DADOS_DIR.mkdir(parents=True, exist_ok=True)
    novo = COOKIES_ARQ.with_name(COOKIES_ARQ.name + ".novo")
    novo.write_text(texto, encoding="utf-8")
    os.replace(novo, COOKIES_ARQ)


def apagar_cookies() -> None:
    COOKIES_ARQ.unlink(missing_ok=True)


# ---------- opções de cada download ----------

FORMATOS_VIDEO = ("mp4", "mkv", "webm", "original")
FORMATOS_AUDIO = ("mp3", "m4a", "original")
KBPS = ("320", "192", "128")


def _segundos(valor) -> float | None:
    """'1:02:03', '2:03', '75' ou número → segundos. Vazio → None."""
    if valor is None or valor == "":
        return None
    if isinstance(valor, (int, float)):
        segundos = float(valor)
    else:
        partes = str(valor).strip().replace(",", ".").split(":")
        if not 1 <= len(partes) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in partes):
            raise DownloadError(f"Tempo inválido: {valor!r}. Use horas:minutos:segundos, por exemplo 1:05:30.")
        segundos = 0.0
        for p in partes:
            segundos = segundos * 60 + float(p)
    if segundos < 0 or segundos > 60 * 60 * 48:
        raise DownloadError("Tempo fora do vídeo.")
    return segundos


def _nome_de_pasta(nome: str | None) -> str:
    """Nome de pasta seguro no Windows (playlists); vazio = pasta principal."""
    if not nome:
        return ""
    limpo = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", str(nome)).strip(" .")
    limpo = re.sub(r"\s+", " ", limpo)[:80].strip(" .")
    # Nomes reservados do Windows, também com extensão ou espaço ("CON .x", "COM¹", "CONIN$")
    base = limpo.split(".")[0].strip().upper()
    if re.fullmatch(r"CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|(COM|LPT)[0-9¹²³]", base):
        limpo = "_" + limpo
    return limpo


def validar_opcoes(o: dict | None) -> dict:
    o = dict(o or {})
    tipo = o.get("tipo", "video")
    if tipo not in ("video", "audio"):
        raise DownloadError("Escolha vídeo ou áudio.")
    qualidade = str(o.get("qualidade", "best"))
    if qualidade != "best" and not qualidade.isdigit():
        raise DownloadError("Qualidade inválida.")
    formato_video = o.get("formato_video", "mp4")
    formato_audio = o.get("formato_audio", "mp3")
    kbps = str(o.get("kbps", "192"))
    if formato_video not in FORMATOS_VIDEO or formato_audio not in FORMATOS_AUDIO or kbps not in KBPS:
        raise DownloadError("Formato inválido.")
    legendas = o.get("legendas") or []
    if not isinstance(legendas, list) or len(legendas) > 10 or not all(
            isinstance(c, str) and re.fullmatch(r"[A-Za-z0-9_.*-]{1,30}", c) for c in legendas):
        raise DownloadError("Idioma de legenda inválido.")
    modo = o.get("legendas_modo", "embutir")
    if modo not in ("embutir", "arquivo"):
        raise DownloadError("Escolha como salvar as legendas.")
    if tipo == "audio" or formato_video == "webm":
        modo = "arquivo"   # áudio não tem onde mostrar legenda; WebM não aceita legenda SRT embutida
    inicio, fim = _segundos(o.get("inicio")), _segundos(o.get("fim"))
    if inicio is not None and fim is not None and fim <= inicio:
        raise DownloadError("O fim do trecho precisa ser depois do início.")
    return {
        "tipo": tipo, "qualidade": qualidade, "formato_video": formato_video, "formato_audio": formato_audio,
        "kbps": kbps, "legendas": legendas, "legendas_modo": modo,
        "inicio": inicio, "fim": fim, "corte_exato": bool(o.get("corte_exato")),
        "capa": bool(o.get("capa", True)),
        "conta": o.get("conta") if o.get("conta") in CONTAS else "nenhuma",
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


def opcoes_ytdlp(o: dict) -> dict:
    """Opções do yt-dlp para o que a pessoa escolheu (sem a pasta, os ganchos e o Referer)."""
    opts: dict = {"postprocessors": []}
    pps = opts["postprocessors"]
    if o["tipo"] == "audio":
        opts["format"] = "ba/b"
        if HAS_FFMPEG:
            codec = "best" if o["formato_audio"] == "original" else o["formato_audio"]
            pps.append({"key": "FFmpegExtractAudio", "preferredcodec": codec,
                        "preferredquality": o["kbps"] if codec == "mp3" else None, "nopostoverwrites": False})
    else:
        opts["format"] = _format_selector(o["qualidade"])
        fv = o["formato_video"]
        if HAS_FFMPEG and fv != "original":
            # MP4 abre em qualquer aparelho: prefere H.264 + AAC dentro da qualidade escolhida.
            if fv == "mp4":
                opts["format_sort"] = ["res", "vcodec:h264", "acodec:aac"]
            elif fv == "webm":
                opts["format_sort"] = ["res", "vcodec:vp9", "acodec:opus"]
            opts["merge_output_format"] = fv
            # O formato final é garantido depois, em converter_formato(): troca só o "envelope" e, se o formato
            # escolhido não aceitar os codecs do vídeo, recodifica.
        elif HAS_FFMPEG:
            opts["merge_output_format"] = "mp4/mkv"

    if o["legendas"]:
        opts.update({"writesubtitles": True, "writeautomaticsub": True, "subtitleslangs": o["legendas"],
                     "subtitlesformat": "srt/vtt/best"})
        if HAS_FFMPEG:
            pps.append({"key": "FFmpegSubtitlesConvertor", "format": "srt", "when": "before_dl"})
            if o["legendas_modo"] == "embutir":
                pps.append({"key": "FFmpegEmbedSubtitle", "already_have_subtitle": False})

    if o["inicio"] is not None or o["fim"] is not None:
        inicio = o["inicio"] or 0.0
        fim = o["fim"] if o["fim"] is not None else float("inf")
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, [(inicio, fim)])
        # áudio é recodificado de qualquer jeito: o corte exato sai quase de graça
        opts["force_keyframes_at_cuts"] = o["corte_exato"] or o["tipo"] == "audio"
        # o ffmpeg corta melhor um arquivo direto que um vídeo em pedaços (HLS): prefere o direto, mesma qualidade
        sort = opts.get("format_sort") or ["res"]
        opts["format_sort"] = [sort[0], "proto", *sort[1:]]

    if o["capa"] and HAS_FFMPEG:
        # Num trecho, os capítulos do vídeo inteiro cairiam fora do arquivo cortado: só título, canal e data.
        corte = o["inicio"] is not None or o["fim"] is not None
        pps.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": not corte})
        formato_final = o["formato_audio"] if o["tipo"] == "audio" else o["formato_video"]
        if formato_final != "webm":   # WebM não aceita capa embutida
            opts["writethumbnail"] = True
            pps.append({"key": "EmbedThumbnail", "already_have_thumbnail": False})
    return opts


# ---------- formato final ----------

RECODIFICAR = {
    "mp4": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-c:s", "mov_text", "-movflags", "+faststart"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-deadline", "good", "-cpu-used", "4",
             "-c:a", "libopus", "-b:a", "160k", "-sn"],
    "mkv": ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-c:s", "srt"],
}


def _ffmpeg(args: list[str], job: "Job") -> bool:
    """Roda o ffmpeg; para se a pessoa cancelar. True se deu certo."""
    proc = subprocess.Popen([FFMPEG_BIN, "-hide_banner", "-loglevel", "error", "-y", *args], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
    while True:
        try:
            proc.wait(timeout=0.5)
            break
        except subprocess.TimeoutExpired:
            if job.cancelar:
                proc.kill()
                proc.wait()
                raise Cancelado()
    if proc.returncode != 0:
        erro = proc.stderr.read().decode("utf-8", "replace").strip().splitlines()[-3:] if proc.stderr else []
        log.warning("ffmpeg falhou (%s): %s", " ".join(args[-6:]), " | ".join(erro))
    return proc.returncode == 0


def converter_formato(arquivo: Path, formato: str, job: "Job") -> Path:
    """Garante o formato escolhido (MP4, MKV ou WebM). Primeiro só troca o "envelope" (instantâneo, sem perder
    qualidade); se o formato não aceitar os codecs do vídeo, recodifica (mais lento)."""
    if arquivo.suffix.lower() == "." + formato:
        return arquivo
    saida = arquivo.with_suffix("." + formato)
    job.status, job.stage = "processing", "ajustando o formato"
    envelope = ["-map", "0", "-dn", "-c", "copy"] + (["-c:s", "mov_text", "-movflags", "+faststart"] if formato == "mp4" else [])
    if formato == "webm":
        envelope = ["-map", "0:v", "-map", "0:a?", "-c", "copy"]
    if _ffmpeg(["-i", str(arquivo), *envelope, str(saida)], job):
        arquivo.unlink(missing_ok=True)
        return saida
    saida.unlink(missing_ok=True)
    job.stage = "convertendo o vídeo (mais demorado)"
    mapa = ["-map", "0:v:0", "-map", "0:a?"] + ([] if formato == "webm" else ["-map", "0:s?"])
    if _ffmpeg(["-i", str(arquivo), *mapa, *RECODIFICAR[formato], str(saida)], job):
        arquivo.unlink(missing_ok=True)
        return saida
    saida.unlink(missing_ok=True)
    raise DownloadError(f"Não consegui converter o vídeo para {formato.upper()}. Tente o formato Original.")


def tem_midia(arquivo: Path) -> bool:
    """O arquivo tem alguma faixa de vídeo ou áudio que dá para ler? (um trecho que falhou sai vazio)"""
    # só vídeo e áudio: legenda e capa não contam (e a saída "null" não sabe gravar legenda)
    r = subprocess.run([FFMPEG_BIN, "-v", "error", "-i", str(arquivo), "-map", "0:V?", "-map", "0:a?", "-t", "1",
                        "-f", "null", "-"],
                       capture_output=True, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, timeout=120)
    return r.returncode == 0


def cortar(arquivo: Path, o: dict, job: "Job") -> Path:
    """Corta o arquivo já baixado (plano B, quando o site não deixa baixar só o trecho)."""
    job.status, job.stage = "processing", "cortando o trecho"
    tempo = ["-ss", str(o["inicio"] or 0)] + (["-to", str(o["fim"])] if o["fim"] is not None else [])
    saida = arquivo.with_name(arquivo.stem + ".cortado" + arquivo.suffix)
    ext = arquivo.suffix.lower().lstrip(".")
    if o["corte_exato"] or o["tipo"] == "audio":
        codecs = {"mp3": ["-c:a", "libmp3lame", "-b:a", f'{o["kbps"]}k'], "m4a": ["-c:a", "aac", "-b:a", "192k"],
                  **RECODIFICAR}.get(ext, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac"])
        if o["tipo"] == "audio":   # a capa (imagem) vai junto, sem recodificar
            args = ["-i", str(arquivo), *tempo, "-map", "0", "-dn", *codecs, "-c:v", "copy"]
        else:                      # 0:V = só o vídeo de verdade (a capa embutida não entra na recodificação)
            args = ["-i", str(arquivo), *tempo, "-map", "0:V", "-map", "0:a?", "-map", "0:s?", "-dn", *codecs]
    else:
        args = [*tempo, "-i", str(arquivo), "-map", "0", "-dn", "-c", "copy"]
    if not _ffmpeg([*args, str(saida)], job) or not tem_midia(saida):
        saida.unlink(missing_ok=True)
        raise DownloadError("Não consegui cortar o trecho. Confira o início e o fim.")
    arquivo.unlink(missing_ok=True)
    saida.replace(arquivo)
    return arquivo


# ---------- downloads ----------

@dataclass
class Job:
    id: str
    url: str
    opcoes: dict
    referer: str | None = None  # página onde o player está (vídeo encontrado dentro de uma página)
    pasta: str = ""             # subpasta (nome da playlist); vazio = pasta principal
    status: str = "queued"      # queued | downloading | processing | finished | error | canceled
    title: str | None = None
    thumbnail: str | None = None
    progress: float = 0.0       # 0-100 do arquivo atual
    stage: str = ""             # ex.: "vídeo", "áudio"
    speed: float | None = None  # bytes/s
    eta: int | None = None      # segundos
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    filename: str | None = None
    filesize: int | None = None
    extras: list = field(default_factory=list)   # legendas salvas como arquivo, ao lado do vídeo
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    cancelar: bool = False

    @property
    def quality(self) -> str:
        return self.opcoes.get("qualidade", "best")

    @property
    def folder(self) -> Path:
        return TEMP_DIR / self.id

    @property
    def destino(self) -> Path:
        return DOWNLOAD_DIR / self.pasta if self.pasta else DOWNLOAD_DIR

    @property
    def path(self) -> Path | None:
        return self.destino / self.filename if self.filename else None

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("cancelar")
        d["quality"] = self.quality
        return d


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=MAX_PARALLEL)
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        # Sobras de downloads interrompidos (app fechado no meio) e cópias de cookies que ficaram
        shutil.rmtree(TEMP_DIR, ignore_errors=True)
        shutil.rmtree(COPIAS_COOKIES, ignore_errors=True)

    def create(self, url: str, opcoes: dict | None = None, referer: str | None = None, title: str | None = None,
               thumbnail: str | None = None, pasta: str | None = None) -> Job:
        url = validate_url(url)
        referer = validate_url(referer) if referer else None
        job = Job(id=uuid.uuid4().hex[:12], url=url, opcoes=validar_opcoes(opcoes), referer=referer,
                  pasta=_nome_de_pasta(pasta),
                  title=(title or None) and str(title)[:300],
                  thumbnail=thumbnail if thumbnail and urlparse(thumbnail).scheme in ("http", "https") else None)
        with self._lock:
            self._jobs[job.id] = job
        self._pool.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if job and job.status in ATIVOS:
            job.cancelar = True
            if job.status == "queued":
                job.status = "canceled"
        return job

    def remover_concluidos(self) -> int:
        with self._lock:
            fora = [k for k, j in self._jobs.items() if j.status not in ATIVOS]
            for k in fora:
                del self._jobs[k]
        return len(fora)

    def ocupado(self) -> bool:
        with self._lock:
            return any(j.status in ATIVOS for j in self._jobs.values())

    def all(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda j: j.created_at, reverse=True)

    # ---- execução -------------------------------------------------------

    def _run(self, job: Job) -> None:
        if job.cancelar:
            job.status = "canceled"
            return
        job.folder.mkdir(parents=True, exist_ok=True)

        def progress_hook(d: dict) -> None:
            if job.cancelar:
                raise Cancelado()
            status = d.get("status")
            if status == "downloading":
                if job.status != "downloading":
                    job.status = "downloading"
                info = d.get("info_dict") or {}
                job.title = job.title or info.get("title")
                job.thumbnail = job.thumbnail or info.get("thumbnail")
                if info.get("vcodec") not in (None, "none"):
                    job.stage = "vídeo"
                elif info.get("acodec") not in (None, "none"):
                    job.stage = "áudio"
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                done = d.get("downloaded_bytes") or 0
                job.total_bytes = int(total) if total else None
                job.downloaded_bytes = int(done)
                job.progress = round(min(done / total * 100, 100), 1) if total else 0.0
                job.speed = d.get("speed")
                job.eta = d.get("eta")
            elif status == "finished":
                job.progress = 100.0
                job.speed = None
                job.eta = None

        def postprocessor_hook(d: dict) -> None:
            if job.cancelar:
                raise Cancelado()
            if d.get("status") == "started":
                job.status = "processing"
                job.stage = {"FFmpegExtractAudio": "convertendo o áudio", "FFmpegVideoRemuxer": "ajustando o formato",
                             "FFmpegEmbedSubtitle": "pondo as legendas", "EmbedThumbnail": "pondo a capa",
                             "FFmpegMerger": "juntando vídeo e áudio"}.get(d.get("postprocessor"), "finalizando")

        try:
            base = _base_opts(job.referer, job.opcoes.get("conta", "nenhuma"))
        except DownloadError as exc:   # ex.: escolheu cookies.txt e o arquivo foi apagado
            job.status, job.error = "error", str(exc)
            shutil.rmtree(job.folder, ignore_errors=True)
            return
        corte = job.opcoes["inicio"] is not None or job.opcoes["fim"] is not None
        nome = "%(title).140B [%(id)s]" + (" (trecho)" if corte else "") + ".%(ext)s"
        opts = {
            **base,
            **opcoes_ytdlp(job.opcoes),
            # Vídeos em pedacinhos (HLS/DASH, como o Vimeo): baixa 4 pedaços por vez em vez de um.
            "concurrent_fragment_downloads": 4,
            "outtmpl": {"default": str(job.folder / nome)},
            "restrictfilenames": False,
            "windowsfilenames": True,
            "progress_hooks": [progress_hook],
            "postprocessor_hooks": [postprocessor_hook],
        }

        try:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(job.url, download=True)
                    job.title = info.get("title") or job.title
                principal, legendas = self._saidas(job.folder)
            except yt_dlp.utils.DownloadError as exc:
                # Trecho vazio faz o passo seguinte (capa, informações) falhar: tenta o plano B abaixo.
                if not corte or job.cancelar or isinstance((exc.exc_info or (None, None))[1], Cancelado):
                    raise
                principal, legendas = None, []
            o = job.opcoes
            if corte and HAS_FFMPEG and (principal is None or not tem_midia(principal)):
                # Alguns sites (vídeo em pedaços) não deixam baixar só o trecho: baixa inteiro e corta aqui.
                for f in job.folder.iterdir():
                    f.unlink(missing_ok=True)
                inteiro = {k: v for k, v in opts.items() if k not in ("download_ranges", "force_keyframes_at_cuts")}
                job.stage = "baixando o vídeo inteiro para cortar"
                with yt_dlp.YoutubeDL(inteiro) as ydl:
                    ydl.extract_info(job.url, download=True)
                principal, legendas = self._saidas(job.folder)
                if principal is not None:
                    principal = cortar(principal, o, job)
            if job.cancelar:   # pediu para cancelar no último passo: descarta (a pasta temporária é apagada abaixo)
                raise Cancelado()
            if principal is None:
                raise DownloadError("O download terminou, mas o arquivo não foi encontrado.")
            if o["tipo"] == "video" and o["formato_video"] != "original" and HAS_FFMPEG:
                principal = converter_formato(principal, o["formato_video"], job)
            if job.cancelar:
                raise Cancelado()
            destino, job.extras = self._mover_conjunto(principal, legendas, job.destino)
            job.filename = destino.name
            job.filesize = destino.stat().st_size
            job.progress = 100.0
            job.stage = ""
            job.status = "finished"
        except Cancelado:
            job.status = "canceled"
            job.stage = ""
        except yt_dlp.utils.DownloadError as exc:
            if job.cancelar or isinstance((exc.exc_info or (None, None))[1], Cancelado):
                job.status = "canceled"
                job.stage = ""
            else:
                job.status = "error"
                job.error = _amigavel(_clean_error(exc), job.url, job.opcoes.get("conta", "nenhuma"))
        except Exception as exc:  # noqa: BLE001 - mostrar qualquer falha ao usuário
            job.status = "error"
            job.error = str(exc) or exc.__class__.__name__
        finally:
            _soltar(base)
            shutil.rmtree(job.folder, ignore_errors=True)
            try:
                TEMP_DIR.rmdir()  # some quando não há outro download em andamento
            except OSError:
                pass

    _move_lock = threading.Lock()

    def _mover_conjunto(self, principal: Path, legendas: list[Path], pasta: Path) -> tuple[Path, list[str]]:
        """Move o arquivo e as legendas com o mesmo nome, sem sobrescrever nada: "Nome (2).mp4" e "Nome (2).pt.srt"
        se algum deles já existir (o player só acha a legenda se o nome for igual ao do vídeo)."""
        with self._move_lock:
            pasta.mkdir(parents=True, exist_ok=True)
            raiz = principal.stem
            # "Video [id].pt.srt" → ".pt.srt" (o yt-dlp sempre põe o idioma antes da extensão)
            sufixos = [principal.suffix] + ["".join(leg.suffixes[-2:]) for leg in legendas]
            nome, n = raiz, 2
            while any((pasta / (nome + suf)).exists() for suf in sufixos):
                nome, n = f"{raiz} ({n})", n + 1
            destino = pasta / (nome + principal.suffix)
            shutil.move(str(principal), destino)
            extras = []
            for leg, suf in zip(legendas, sufixos[1:]):
                shutil.move(str(leg), pasta / (nome + suf))
                extras.append(nome + suf)
            return destino, extras

    @staticmethod
    def _saidas(folder: Path) -> tuple[Path | None, list[Path]]:
        """O arquivo principal (o maior) e as legendas que ficaram como arquivo. Miniaturas soltas são descartadas."""
        arquivos = [p for p in folder.iterdir()
                    if p.is_file() and not p.name.endswith((".part", ".ytdl", ".temp")) and ".part-Frag" not in p.name]
        legendas = [p for p in arquivos if p.suffix.lower() in EXT_LEGENDA]
        imagens = (".jpg", ".jpeg", ".png", ".webp")
        midia = [p for p in arquivos if p not in legendas and p.suffix.lower() not in imagens]
        return max(midia, key=lambda p: p.stat().st_size, default=None), legendas


manager = JobManager()
