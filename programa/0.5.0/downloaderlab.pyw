# Video Downloader Lab (Windows): o app numa janela do pywebview (WebView2), aberta pelo atalho.
#
#   pythonw downloaderlab.pyw              abre na janela (é o que o atalho faz)
#   pythonw downloaderlab.pyw --navegador  abre no navegador padrão
#   python  downloaderlab.pyw --instalar   o que o "Instalar Video Downloader Lab.bat" roda: arquivos do app, pip,
#                                          componentes, ffmpeg e atalhos
#
# O Python é o portátil oficial do python.org, só do Video Downloader Lab, em python/ (o instalador baixa e confere).
#
# Sobe o servidor do app (FastAPI + yt-dlp, pasta app/) só em 127.0.0.1: nada fica acessível pela rede. Os vídeos vão
# para a pasta Downloads\Video Downloader Lab. As mensagens vão para logs/downloaderlab.log.
#
# Instalação e atualização — mesmo modelo do Vídeo Lab IA (atualizacao.json no repositório PÚBLICO de versões):
#   verificar()            consulta a versão publicada.
#   instalar_atualizacao() baixa os arquivos do app listados no manifesto, confere tudo e só então troca.
#   restaurar()            volta para a versão anterior (guardada em versoes-anteriores/).
# A página consulta, instala e volta de versão pelo botão de atualização (rotas /api/atualizacao do app/main.py, com o
# TOKEN que só a página entregue por este app recebe); se o código Python mudou, o app reabre sozinho (reiniciar).
# arquivos-do-app.txt lista o que é código do app: a atualização troca o que o manifesto traz e remove só o que estava
# na lista e saiu do pacote. Nunca são tocados: python/, logs/, dados/, versoes-anteriores/ e ffmpeg/.
# O yt-dlp (que precisa acompanhar as mudanças dos sites) é atualizado pelo pip uma vez por dia, ao fechar o app.
import ast
import gzip
import hashlib
import html
import json
import logging
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import webbrowser
import zipfile
from pathlib import Path, PurePosixPath, PureWindowsPath
from urllib.parse import quote, urlsplit, urlunsplit

APP = Path(__file__).resolve().parent
PORTA = 47831            # fixa: as preferências da página (guardadas por endereço) não se perdem
TITULO = 'Video Downloader Lab'
PROGRAMA = APP / 'downloaderlab.pyw'
# Se mudarem numa atualização, o pip roda de novo. deno: o yt-dlp precisa de um motor de JavaScript para o YouTube.
REQUISITOS = ['yt-dlp[default]>=2026.10.26', 'fastapi>=0.115,<1', 'uvicorn>=0.30,<1', 'pywebview>=6.2,<7', 'deno>=2.5,<3']
DADOS = APP / 'dados'   # dados do WebView2, marca dos atalhos e data da última atualização do yt-dlp
ICONE = APP / 'downloaderlab-2.ico'   # nome novo a cada troca de ícone: o Windows guarda ícones em cache pelo caminho
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
# Caminho completo: só o nome faria o Windows procurar antes na pasta do Python e na pasta atual.
_PS = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
POWERSHELL = str(_PS) if _PS.is_file() else 'powershell'
ATALHO = 'Video Downloader Lab.lnk'   # Área de Trabalho e Menu Iniciar
ATALHO_PASTA = APP / 'Abrir Video Downloader Lab.lnk'   # para quem abre a pasta saber em que clicar
MARCA_ATALHOS = f'{ICONE.name} {ATALHO} maximizada'   # mudou o ícone, o nome ou o jeito do atalho: refaz os atalhos ao abrir

REPO_TESTE = os.environ.get('DOWNLOADERLAB_REPO_VERSOES')   # testes: outro repositório (ex.: http://127.0.0.1:8000/)
REPO_VERSOES = REPO_TESTE or 'https://raw.githubusercontent.com/rla-devv/video-downloader-lab-versoes/main/'
VERSAO_ARQ = APP / 'app' / '__init__.py'   # __version__ = "X.Y.Z"
ANTERIORES = APP / 'versoes-anteriores'
LISTA = APP / 'arquivos-do-app.txt'
LISTA_CABECALHO = '# Arquivos do Video Downloader Lab. A atualização troca e remove só o que está nesta lista.\n'
PROTEGIDAS = {'python', 'logs', 'dados', 'versoes-anteriores', 'ffmpeg'}

# O Python portátil vem sem pip: instala o pacote oficial do pip (PyPI), conferido pelo SHA-256.
PIP = ('https://files.pythonhosted.org/packages/f3/6e/1736e5b4ae2b778ef2f81c47d797de9f891d4d8acb047a24ca37a60294dd/'
       'pip-26.2.1-py3-none-any.whl', '71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e', 1_816_632)
# O pip só usa pacotes prontos (.whl): o Python portátil não monta pacote que só existe como código-fonte.
# O proxy_tools (exigido pelo pywebview) é assim no PyPI; a versão pronta fica no repositório de versões.
BIBLIOTECAS = {'proxy_tools-0.1.0-py3-none-any.whl': ('5715de0f918322bba8ae5a4320af457fc5aed17b6b7d214a1438768c9cc67f8f', 2_936)}
VER_RE = re.compile(r'^\d+(\.\d+){1,3}$')

# ffmpeg (junta vídeo e áudio, converte): build "essentials" do gyan.dev, com versão fixa. Só grava o que confere
# em tamanho e SHA-256 (o mesmo publicado pelo gyan.dev). O GitHub do gyan.dev é a reserva.
FFMPEG = APP / 'ffmpeg'
FFMPEG_VERSAO = '9.0.2'
FFMPEG_ZIP = ('ffmpeg-9.0.2-essentials_build.zip', '60f467265b1e312373dbcd92200c2618a74850f98d3d078e94296bb3fa2047ba',
              114_768_076)
FFMPEG_ORIGENS = ('https://www.gyan.dev/ffmpeg/builds/packages/',
                  'https://github.com/GyanD/codexffmpeg/releases/download/9.0.2/')
FFMPEG_ARQUIVOS = ('bin/ffmpeg.exe', 'bin/ffprobe.exe', 'LICENSE')
YTDLP_INTERVALO = 24 * 3600


def limitar_log(arquivo, maximo=256 * 1024, fica=64 * 1024):
    """Log passou do máximo: fica só o fim (a partir de uma linha inteira), para não crescer sem fim."""
    try:
        if arquivo.stat().st_size <= maximo:
            return
        with open(arquivo, 'rb') as f:
            f.seek(-fica, os.SEEK_END)
            fim = f.read()
        fim = fim[fim.find(b'\n') + 1:]
        novo = arquivo.with_name(arquivo.name + '.novo')
        novo.write_bytes(fim)
        os.replace(novo, arquivo)
    except OSError:
        pass   # log aberto por outro processo ou sem permissão: segue sem limitar


(APP / 'logs').mkdir(exist_ok=True)
limitar_log(APP / 'logs' / 'downloaderlab.log')
logging.basicConfig(filename=APP / 'logs' / 'downloaderlab.log', level=logging.INFO, encoding='utf-8',
                    format='%(asctime)s %(levelname)s %(name)s %(message)s')
log = logging.getLogger('downloaderlab')
if sys.stdout is None or sys.stderr is None:    # pythonw: sem janela preta, tudo vai para o log
    sys.stdout = sys.stderr = open(APP / 'logs' / 'downloaderlab.log', 'a', encoding='utf-8', buffering=1)

if os.name == 'nt':
    # Barra de tarefas: sem isto o Windows agrupa a janela com o pythonw.exe e mostra o ícone do Python.
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('RLAdev.VideoDownloaderLab')
    except Exception:
        log.exception('não consegui definir o ID do app na barra de tarefas')

_cache = {'quando': 0.0, 'manifesto': None}
TOKEN = secrets.token_urlsafe(24)   # só a página entregue por este app pode pedir atualização
TROCANDO_VERSAO = threading.Lock()   # uma troca de versão por vez


# ---------- downloads ----------

def _sha(dados):
    return hashlib.sha256(dados).hexdigest()


def _abrir_url(url):
    partes = urlsplit(url)
    if partes.scheme not in ('https', 'http'):   # nada de file:// ou outros esquemas
        raise ValueError(f'endereço não permitido: {url[:80]}')
    url = urlunsplit(partes._replace(path=quote(partes.path, safe='/%')))   # espaço e acento no endereço
    req = urllib.request.Request(url, headers={'User-Agent': 'VideoDownloaderLab', 'Cache-Control': 'no-cache',
                                               'Accept-Encoding': 'gzip'})
    r = urllib.request.urlopen(req, timeout=60, context=ssl.create_default_context())
    return r, (gzip.GzipFile(fileobj=r) if r.headers.get('Content-Encoding') == 'gzip' else r)


def _baixar(url, limite):
    r, fonte = _abrir_url(url)
    with r:
        partes, total = [], 0
        while total <= limite:
            bloco = fonte.read(1 << 20)
            if not bloco:
                break
            partes.append(bloco)
            total += len(bloco)
    if total > limite:
        raise ValueError('arquivo maior que o esperado')
    return b''.join(partes)


def _baixar_para_arquivo(url, destino, tamanho, sha, progresso=None):
    """Arquivo grande: grava em disco aos poucos e confere tamanho e SHA-256 no fim. Não conferiu: apaga."""
    h, total = hashlib.sha256(), 0
    r, fonte = _abrir_url(url)
    try:
        with r, open(destino, 'wb') as f:
            while True:
                bloco = fonte.read(1 << 20)
                if not bloco:
                    break
                total += len(bloco)
                if total > tamanho:
                    raise ValueError('arquivo maior que o esperado')
                h.update(bloco)
                f.write(bloco)
                if progresso:
                    progresso(total / tamanho)
        if total != tamanho or h.hexdigest() != sha:
            raise ValueError('o arquivo baixado não confere com o esperado (SHA-256)')
    except BaseException:
        Path(destino).unlink(missing_ok=True)
        raise


# ---------- versões ----------

def _ver(v):
    return tuple(int(x) for x in v.split('.'))


def versao_de(texto):
    m = re.search(r'^__version__ = "(\d+(?:\.\d+){1,3})"', texto, re.M)
    return m.group(1) if m else '0.0.0'


def versao_instalada():
    try:
        return versao_de(VERSAO_ARQ.read_text(encoding='utf-8'))
    except OSError:
        return '0.0.0'


def _caminho_seguro(caminho):
    """Todo caminho tem de ficar dentro da pasta do app: sem .., sem caminho absoluto, sem ':'. As pastas protegidas
    valem como o Windows as enxerga: sem diferença de maiúsculas e sem ponto ou espaço no fim do nome."""
    p = PurePosixPath(caminho or '.')
    nomes = [n.lower().rstrip(' .') for n in p.parts]
    if (not caminho or '\\' in caminho or ':' in caminho or '\x00' in caminho or p.is_absolute() or not p.parts
            or '..' in p.parts or '' in nomes or nomes[0] in PROTEGIDAS):
        raise ValueError(f'caminho não permitido no pacote: {caminho!r}')
    alvo = (APP / p).resolve()
    if APP not in alvo.parents:
        raise ValueError(f'caminho fora da pasta do app: {caminho!r}')
    return alvo


def _manifesto(forcar=False):
    if not forcar and _cache['manifesto'] and time.time() - _cache['quando'] < 300:
        return _cache['manifesto']
    # ?t= muda o endereço (o GitHub guarda cópia de cada um por uns 5 min); ao forçar, um endereço único.
    chave = int(time.time() * 1000) if forcar else int(time.time() // 60)
    m = json.loads(_baixar(REPO_VERSOES + f'atualizacao.json?t={chave}', 256_000))
    if not (isinstance(m, dict) and VER_RE.match(str(m.get('versao', ''))) and isinstance(m.get('arquivos'), list)):
        raise ValueError('atualizacao.json inválido')
    nov = m.get('novidades') or []
    m['novidades'] = [n for n in (nov if isinstance(nov, list) else [nov]) if isinstance(n, str) and n.strip()][:8]
    _cache.update(quando=time.time(), manifesto=m)
    return m


def _lista_de_arquivos(m):
    lista = []
    for it in m['arquivos']:
        if not (isinstance(it, dict) and isinstance(it.get('caminho'), str) and isinstance(it.get('origem'), str)
                and re.fullmatch(r'[0-9a-f]{64}', str(it.get('sha256', ''))) and isinstance(it.get('tamanho'), int)
                and 0 <= it['tamanho'] < 64_000_000 and not it['origem'].startswith(('/', '.')) and '..' not in it['origem']
                and '\\' not in it['origem'] and ':' not in it['origem']):
            raise ValueError('lista de arquivos do manifesto inválida')
        _caminho_seguro(it['caminho'])
        lista.append(it)
    caminhos = {it['caminho'] for it in lista}
    if not {'downloaderlab.pyw', 'app/__init__.py', 'app/main.py'} <= caminhos:
        raise ValueError('o pacote não traz o programa completo')
    return lista


def _lista_atual():
    try:
        linhas = LISTA.read_text(encoding='utf-8').splitlines()
    except OSError:
        linhas = []
    return {l.strip() for l in linhas if l.strip() and not l.lstrip().startswith('#')}


def _escrever_lista(caminhos):
    novo = LISTA.with_name(LISTA.name + '.novo')
    novo.write_text(LISTA_CABECALHO + ''.join(c + '\n' for c in sorted(caminhos)), encoding='utf-8')
    os.replace(novo, LISTA)


def _saem_do_pacote(novos):
    """Arquivos do app (da lista) que o pacote novo não traz mais."""
    saem = []
    for caminho in sorted(_lista_atual() - set(novos)):
        try:
            alvo = _caminho_seguro(caminho)
        except ValueError:
            log.warning('arquivos-do-app.txt: caminho ignorado: %r', caminho)
            continue
        if alvo.is_file():
            saem.append(caminho)
    return saem


def _limpar_pastas_vazias(caminhos):
    for caminho in caminhos:
        pasta = (APP / caminho).parent
        while pasta != APP and pasta.is_dir() and not any(pasta.iterdir()):
            pasta.rmdir()
            pasta = pasta.parent


def verificar(forcar=False):
    atual = versao_instalada()
    m = _manifesto(forcar)
    nova = {'versao': m['versao'], 'novidades': m['novidades']} if _ver(m['versao']) > _ver(atual) else None
    return {'atual': atual, 'nova': nova, 'anterior': versao_anterior()}


def _guardadas():
    """Pastas de versões guardadas (com versao.txt), da mais antiga para a mais nova."""
    return sorted((d for d in ANTERIORES.glob('*') if d.is_dir() and (d / 'versao.txt').is_file() and VER_RE.match(d.name)),
                  key=lambda d: _ver(d.name))


def versao_anterior():
    """A versão para a qual dá para voltar ("Voltar para a versão X"), ou None."""
    try:
        guardadas = _guardadas()
        return guardadas[-1].name if guardadas else None
    except OSError:
        return None


def _requisitos_de(programa):
    """A lista REQUISITOS escrita no programa baixado (só lida, nunca executada)."""
    # A lista inteira da linha (os nomes têm colchetes, como yt-dlp[default]); comentário no fim é ignorado.
    m = re.search(r'^REQUISITOS = (\[.*\])[ \t]*(?:#[^\]\n]*)?$', programa, re.M)
    req = ast.literal_eval(m.group(1)) if m else REQUISITOS
    if not (isinstance(req, list) and all(isinstance(r, str) and re.fullmatch(r'[A-Za-z0-9_.\-\[\]<>=!~, ]+', r) for r in req)):
        raise ValueError('requisitos inválidos no programa novo')
    return req


def _baixar_conferido(nome, origem, tamanho, sha):
    dados = _baixar(REPO_VERSOES + origem, tamanho)
    if len(dados) != tamanho or _sha(dados) != sha:
        raise ValueError(f'{nome}: o conteúdo baixado não confere com o anunciado (SHA-256)')
    return dados


def instalar_atualizacao():
    """Baixa e confere todos os arquivos; só então troca. Se qualquer passo falhar, tudo volta como estava."""
    atual = versao_instalada()
    m = _manifesto(forcar=True)
    if _ver(m['versao']) <= _ver(atual):
        raise ValueError(f'a versão publicada ({m["versao"]}) não é mais nova que a instalada ({atual})')
    lista = _lista_de_arquivos(m)

    # 1. Baixa e confere tudo antes de tocar em qualquer arquivo.
    novos = {it['caminho']: _baixar_conferido(it['caminho'], it['origem'], it['tamanho'], it['sha256']) for it in lista}
    if versao_de(novos['app/__init__.py'].decode('utf-8')) != m['versao']:
        raise ValueError('a versão escrita dentro do pacote não é a anunciada')
    requisitos = _requisitos_de(novos['downloaderlab.pyw'].decode('utf-8'))

    # 2. Troca só o que mudou e remove o que saiu do pacote, guardando a versão atual de cada arquivo para poder voltar.
    copia = ANTERIORES / atual
    trocados, criados, removidos = [], [], []
    pip_rodou = False
    try:
        for caminho, dados in novos.items():
            alvo = _caminho_seguro(caminho)
            if alvo.is_file() and alvo.read_bytes() == dados:
                continue   # igual ao atual: não reescreve (no Windows, reescrever um .bat em uso dá problema)
            if alvo.is_file():
                guarda = copia / caminho
                guarda.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(alvo, guarda)
                trocados.append(caminho)
            else:
                criados.append(caminho)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            novo = alvo.with_name(alvo.name + '.novo')   # escreve ao lado e renomeia: nunca fica pela metade
            novo.write_bytes(dados)
            os.replace(novo, alvo)
        for caminho in _saem_do_pacote(novos):
            guarda = copia / caminho
            guarda.parent.mkdir(parents=True, exist_ok=True)
            os.replace(_caminho_seguro(caminho), guarda)
            removidos.append(caminho)
        if requisitos != REQUISITOS and atual != '0.0.0':
            pip_rodou = True
            instalar_requisitos(requisitos)
        _escrever_lista(novos)   # dentro do try: se não der para gravar a lista, a troca toda é desfeita
    except Exception:
        if pip_rodou:
            try:
                instalar_requisitos(REQUISITOS)
            except Exception:
                log.exception('não consegui devolver os componentes da versão atual')
        for caminho in trocados:
            if (copia / caminho).exists():
                os.replace(copia / caminho, _caminho_seguro(caminho))
        for caminho in criados:
            _caminho_seguro(caminho).unlink(missing_ok=True)
        for caminho in removidos:
            alvo = _caminho_seguro(caminho)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            os.replace(copia / caminho, alvo)
        if copia.is_dir() and not (copia / 'versao.txt').exists():
            shutil.rmtree(copia, ignore_errors=True)   # cópia de uma tentativa desfeita
        raise
    if atual == '0.0.0':   # instalação nova: não há versão anterior para guardar
        shutil.rmtree(copia, ignore_errors=True)
    else:
        (copia / 'versao.txt').parent.mkdir(parents=True, exist_ok=True)
        (copia / 'versao.txt').write_text(json.dumps({'versao': atual, 'trocados': trocados, 'criados': criados,
                                                      'removidos': removidos}), encoding='utf-8')
        for item in ANTERIORES.glob('*'):   # guarda só a versão anterior mais recente
            if item != copia and item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
    _limpar_pastas_vazias(removidos)
    _cache.update(quando=0.0, manifesto=None)
    mudou = set(trocados + criados)
    return {'versao': m['versao'], 'programa_mudou': 'downloaderlab.pyw' in mudou,
            # código Python novo (programa ou app/) só vale depois de abrir de novo; página e estilo, ao recarregar
            'reiniciar': any(c.endswith(('.py', '.pyw')) for c in mudou | set(removidos)) or requisitos != REQUISITOS,
            'atalhos': any(c.endswith('.ico') for c in mudou | set(removidos))}


def restaurar():
    """Volta para a versão anterior guardada em versoes-anteriores/ (só existe uma). Devolve os arquivos guardados,
    apaga os que a versão atual trouxe e a guardada não tinha e, se a lista REQUISITOS da guardada for outra,
    reinstala os componentes dela. Qualquer falha desfaz tudo. Depois, a cópia usada é apagada (para ter a versão
    mais nova de novo, é só atualizar)."""
    guardadas = _guardadas()
    if not guardadas:
        raise ValueError('não há versão anterior guardada')
    pasta = guardadas[-1]
    info = json.loads((pasta / 'versao.txt').read_text(encoding='utf-8'))
    trocados, criados, removidos = (list(info.get(k, [])) for k in ('trocados', 'criados', 'removidos'))
    for caminho in trocados + removidos:
        _caminho_seguro(caminho)
        if not (pasta / caminho).is_file():
            raise ValueError(f'a cópia guardada está incompleta (falta {caminho})')
    for caminho in criados:
        _caminho_seguro(caminho)
    prog = pasta / 'downloaderlab.pyw'
    requisitos = _requisitos_de(prog.read_text(encoding='utf-8')) if prog.is_file() else REQUISITOS

    agora = {}   # o que está agora em cada caminho que vai mudar, para desfazer se algo falhar
    for caminho in trocados + criados + removidos:
        alvo = _caminho_seguro(caminho)
        agora[caminho] = alvo.read_bytes() if alvo.is_file() else None
    lista_antes = LISTA.read_bytes() if LISTA.is_file() else None
    pip_rodou = False
    try:
        for caminho in trocados + removidos:
            alvo = _caminho_seguro(caminho)
            alvo.parent.mkdir(parents=True, exist_ok=True)
            novo = alvo.with_name(alvo.name + '.novo')
            novo.write_bytes((pasta / caminho).read_bytes())
            os.replace(novo, alvo)
        for caminho in criados:
            _caminho_seguro(caminho).unlink(missing_ok=True)
        _escrever_lista((_lista_atual() - set(criados)) | set(removidos))
        if requisitos != REQUISITOS:
            pip_rodou = True
            instalar_requisitos(requisitos)
    except Exception:
        if pip_rodou:
            try:
                instalar_requisitos(REQUISITOS)
            except Exception:
                log.exception('não consegui devolver os componentes da versão atual')
        for caminho, dados in agora.items():
            alvo = _caminho_seguro(caminho)
            try:
                if dados is None:
                    alvo.unlink(missing_ok=True)
                else:
                    alvo.parent.mkdir(parents=True, exist_ok=True)
                    novo = alvo.with_name(alvo.name + '.novo')
                    novo.write_bytes(dados)
                    os.replace(novo, alvo)
            except OSError:
                log.exception('não consegui desfazer %s', caminho)
        if lista_antes is not None:
            LISTA.write_bytes(lista_antes)
        raise
    shutil.rmtree(pasta, ignore_errors=True)
    _limpar_pastas_vazias(criados)
    _cache.update(quando=0.0, manifesto=None)
    mudou = set(trocados + criados + removidos)
    return {'versao': versao_instalada(),
            'reiniciar': any(c.endswith(('.py', '.pyw')) for c in mudou) or requisitos != REQUISITOS,
            'atalhos': any(c.endswith('.ico') for c in mudou)}


# ---------- componentes (pip) ----------

def _python_console():
    exe = Path(sys.executable)
    alt = exe.with_name('python.exe')
    return str(alt) if exe.name.lower() == 'pythonw.exe' and alt.exists() else str(exe)


def garantir_pip():
    if subprocess.run([_python_console(), '-m', 'pip', '--version'], capture_output=True,
                      creationflags=NO_WINDOW).returncode == 0:
        return
    url, sha, tamanho = PIP
    dados = _baixar(url, tamanho)
    if len(dados) != tamanho or _sha(dados) != sha:
        raise ValueError('pip: o arquivo baixado não confere com o esperado (SHA-256)')
    with tempfile.TemporaryDirectory(prefix='downloaderlab-') as pasta:
        whl = Path(pasta, url.rsplit('/', 1)[1])
        whl.write_bytes(dados)
        # o próprio pacote do pip roda o pip que o instala, como "python -m pip" (no Windows, o pip só
        # aceita trocar a si mesmo assim); o Python portátil ignora PYTHONPATH, daí o sys.path.insert
        roda = 'import runpy, sys; sys.path.insert(0, sys.argv.pop(1)); runpy.run_module("pip", run_name="__main__", alter_sys=True)'
        subprocess.run([_python_console(), '-c', roda, str(whl), 'install', '--no-index', '--quiet',
                        '--no-warn-script-location', str(whl)], check=True)


def _pip(args, console=False, timeout=None):
    subprocess.run([_python_console(), '-m', 'pip', 'install', '--only-binary=:all:', '--quiet',
                    '--disable-pip-version-check', '--no-warn-script-location', *args],
                   check=True, cwd=APP, timeout=timeout, creationflags=0 if console else NO_WINDOW)


def instalar_requisitos(requisitos, console=False):
    with tempfile.TemporaryDirectory(prefix='downloaderlab-') as pasta:
        for nome, (sha, tamanho) in BIBLIOTECAS.items():
            Path(pasta, nome).write_bytes(_baixar_conferido(nome, 'bibliotecas/' + nome, tamanho, sha))
        _pip(['--find-links', pasta, *requisitos], console=console)


def atualizar_ytdlp(console=False):
    """Os sites mudam o tempo todo: o yt-dlp precisa estar sempre na versão mais nova."""
    req = next((r for r in REQUISITOS if r.startswith('yt-dlp')), 'yt-dlp[default]')
    _pip(['--upgrade', '--upgrade-strategy', 'only-if-needed', req], console=console, timeout=600)
    DADOS.mkdir(exist_ok=True)
    (DADOS / 'yt-dlp-atualizado.txt').write_text(str(int(time.time())), encoding='utf-8')


def atualizar_ytdlp_se_preciso():
    marca = DADOS / 'yt-dlp-atualizado.txt'
    try:
        if marca.is_file() and time.time() - int(marca.read_text(encoding='utf-8').strip()) < YTDLP_INTERVALO:
            return
    except (OSError, ValueError):
        pass
    try:
        atualizar_ytdlp()
        log.info('yt-dlp em dia')
    except Exception:
        log.exception('não consegui atualizar o yt-dlp agora; tento de novo na próxima vez')


# ---------- ffmpeg ----------

def ffmpeg_pronto():
    try:
        return ((FFMPEG / 'versao.txt').read_text(encoding='utf-8').strip() == FFMPEG_VERSAO
                and all((FFMPEG / Path(n).name).is_file() for n in FFMPEG_ARQUIVOS))
    except OSError:
        return False


def preparar_ffmpeg(avisar=None):
    """Baixa o ffmpeg (uma vez), confere e extrai só o ffmpeg.exe, o ffprobe.exe e a licença."""
    if ffmpeg_pronto():
        return True
    nome, sha, tamanho = FFMPEG_ZIP
    FFMPEG.mkdir(exist_ok=True)
    zip_tmp = FFMPEG / (nome + '.baixando')
    ultimo = None
    for origem in FFMPEG_ORIGENS:
        try:
            _baixar_para_arquivo(origem + nome, zip_tmp, tamanho, sha, avisar)
            break
        except Exception as e:
            ultimo = e
            log.warning('ffmpeg: %s não deu certo (%s)', origem, e)
    else:
        raise RuntimeError(f'não consegui baixar o ffmpeg: {ultimo}')
    try:
        raiz = nome[:-len('.zip')] + '/'
        with zipfile.ZipFile(zip_tmp) as z:
            for interno in FFMPEG_ARQUIVOS:
                destino = FFMPEG / Path(interno).name
                novo = destino.with_name(destino.name + '.novo')
                with z.open(raiz + interno) as src, open(novo, 'wb') as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                os.replace(novo, destino)
        (FFMPEG / 'versao.txt').write_text(FFMPEG_VERSAO, encoding='utf-8')
    finally:
        zip_tmp.unlink(missing_ok=True)
    return True


# ---------- atalhos ----------

# Só ASCII: o PowerShell do Windows lê o script com a página de código do sistema. Os caminhos chegam
# pelas variáveis de ambiente, então pastas com acento funcionam.
PS_ATALHOS = r'''
# VDL_MODO: todos = instalacao (Area de Trabalho, Menu Iniciar e pasta do app);
#           atualizar = so refaz os atalhos que ja existem (troca de icone ou de nome);
#           pasta = so o atalho da pasta do app.
$ErrorActionPreference = 'Stop'
$app = $env:VDL_PASTA
$modo = $env:VDL_MODO
$shell = New-Object -ComObject WScript.Shell
function Salvar-Atalho($caminho) {
  $atalho = $shell.CreateShortcut($caminho)
  $atalho.TargetPath = $env:VDL_PYTHONW
  $atalho.Arguments = '"' + (Join-Path $app $env:VDL_PROGRAMA) + '"'
  $atalho.WorkingDirectory = $app
  $atalho.IconLocation = $env:VDL_ICONE + ',0'
  $atalho.WindowStyle = 3   # abre maximizada
  $atalho.Description = 'Video Downloader Lab - baixe videos de varias plataformas'
  $atalho.Save()
}
if ($modo -ne 'pasta') {
  foreach ($pasta in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) {
    $caminho = Join-Path $pasta $env:VDL_ATALHO
    if ($modo -eq 'atualizar' -and -not (Test-Path $caminho)) { continue }
    Salvar-Atalho $caminho
  }
}
$naPasta = Join-Path $app $env:VDL_ATALHO_PASTA
if ($modo -ne 'atualizar' -or (Test-Path $naPasta)) { Salvar-Atalho $naPasta }
# Pede ao Windows para redesenhar os icones (sem isto o icone antigo pode continuar aparecendo).
try { Start-Process -FilePath (Join-Path $env:SystemRoot 'System32\ie4uinit.exe') -ArgumentList '-show' -WindowStyle Hidden -Wait } catch { }
'''


def criar_atalhos(modo='todos'):
    """Atalhos "Video Downloader Lab" (Área de Trabalho e Menu Iniciar) e "Abrir Video Downloader Lab" (pasta do app)."""
    fd, script = tempfile.mkstemp(prefix='downloaderlab-atalhos-', suffix='.ps1')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8-sig') as f:
            f.write(PS_ATALHOS)
        env = dict(os.environ, VDL_PASTA=str(APP), VDL_ICONE=str(ICONE), VDL_PROGRAMA=PROGRAMA.name,
                   VDL_PYTHONW=str(Path(sys.executable).with_name('pythonw.exe')), VDL_MODO=modo,
                   VDL_ATALHO=ATALHO, VDL_ATALHO_PASTA=ATALHO_PASTA.name)
        r = subprocess.run([POWERSHELL, '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script],
                           env=env, timeout=60, creationflags=NO_WINDOW, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f'PowerShell terminou com código {r.returncode}')
    finally:
        os.unlink(script)
    if modo != 'pasta':
        DADOS.mkdir(exist_ok=True)
        (DADOS / 'icone-dos-atalhos.txt').write_text(MARCA_ATALHOS, encoding='utf-8')


def atualizar_atalhos(forcar=False):
    """Põe o ícone atual nos atalhos que já existem. Só no Windows."""
    if os.name != 'nt' or not ICONE.is_file():
        return
    marca = DADOS / 'icone-dos-atalhos.txt'
    try:
        if not forcar and marca.is_file() and marca.read_text(encoding='utf-8').strip() == MARCA_ATALHOS:
            return
        criar_atalhos('atualizar')
    except Exception:
        log.exception('não consegui atualizar o ícone dos atalhos')


def garantir_atalho_pasta():
    """Ao abrir: cria o "Abrir Video Downloader Lab" da pasta se ele faltar. Não recria os da Área de Trabalho e do
    Menu Iniciar: a pessoa pode ter apagado de propósito."""
    if os.name != 'nt' or ATALHO_PASTA.exists():
        return
    try:
        criar_atalhos('pasta')
    except Exception:
        log.exception('não consegui criar o atalho na pasta do app')


# ---------- instalação (Instalar Video Downloader Lab.bat) ----------

def instalar_programa():
    """Roda com o python.exe do app, numa janela de console: as mensagens são para a pessoa."""
    # O pacote de instalação só traz o "Instalar Video Downloader Lab.bat" (que baixa este arquivo): o resto do
    # programa vem do repositório de versões, pelo mesmo caminho conferido das atualizações. Já em dia: nada muda.
    m = verificar(forcar=True)
    if m['nova']:
        print(f' Baixando o Video Downloader Lab {m["nova"]["versao"]}...', flush=True)
        r = instalar_atualizacao()
        if r['programa_mudou']:   # veio um programa mais novo: ele continua a instalação
            sys.exit(subprocess.run([sys.executable, str(PROGRAMA), '--instalar']).returncode)

    print(' Instalando os componentes (yt-dlp, servidor do app e janela), cerca de 90 MB...', flush=True)
    garantir_pip()
    instalar_requisitos(REQUISITOS, console=True)
    print(' Conferindo se o yt-dlp está na versão mais nova...', flush=True)
    try:
        atualizar_ytdlp(console=True)
    except Exception:
        log.exception('yt-dlp')
        print('   Não consegui agora: o app tenta de novo sozinho.', flush=True)

    if ffmpeg_pronto():
        print(f' ffmpeg {FFMPEG_VERSAO} já instalado.', flush=True)
    else:
        print(f' Baixando o ffmpeg {FFMPEG_VERSAO} (junta vídeo e áudio; cerca de 110 MB, só na primeira vez)...', flush=True)
        passo = {'ultimo': -1}

        def avisar(fracao):
            pct = int(min(fracao, 1) * 100)
            if pct != passo['ultimo']:
                passo['ultimo'] = pct
                print(f'\r   {pct:3d}%', end='', flush=True)
        try:
            pronto = preparar_ffmpeg(avisar)
        except Exception:
            log.exception('ffmpeg')
            pronto = False
        print('\r   pronto' if pronto else '\r   Não consegui baixar agora: o app tenta de novo sozinho quando abrir.', flush=True)

    if os.name == 'nt':
        print(' Criando os atalhos: "Abrir Video Downloader Lab" nesta pasta e "Video Downloader Lab"'
              ' na Área de Trabalho e no Menu Iniciar...', flush=True)
        criar_atalhos()


# ---------- abrir o app ----------

def pasta_downloads():
    """A pasta Downloads do Windows (mesmo se a pessoa a mudou de lugar) + "Video Downloader Lab"."""
    base = None
    if os.name == 'nt':
        try:
            import ctypes
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD), ('Data3', wintypes.WORD),
                            ('Data4', ctypes.c_ubyte * 8)]
            guid = GUID()
            ctypes.memmove(ctypes.byref(guid), uuid.UUID('374DE290-123F-4565-9164-39C4925E467B').bytes_le, 16)
            caminho = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(caminho)) == 0:
                base = Path(caminho.value)
                ctypes.windll.ole32.CoTaskMemFree(caminho)
        except Exception:
            log.exception('não achei a pasta Downloads pelo Windows; usando a padrão')
    return (base or Path.home() / 'Downloads') / TITULO


def preparar_ambiente():
    """Antes de importar o app: pasta dos vídeos, ffmpeg e deno no PATH, e a pasta do app nos imports."""
    os.environ.setdefault('DOWNLOAD_DIR', str(pasta_downloads()))
    os.environ['VDL_DESKTOP'] = '1'
    os.environ['VDL_TOKEN'] = TOKEN
    os.environ['VDL_DADOS'] = str(DADOS)   # cookies.txt da pessoa (se ela escolher); a atualização nunca mexe aqui
    extras = [str(FFMPEG), str(Path(sys.executable).parent / 'Scripts')]   # ffmpeg.exe e deno.exe
    os.environ['PATH'] = os.pathsep.join(extras + [os.environ.get('PATH', '')])
    # O Python portátil (._pth) não põe a pasta do programa nos imports: põe aqui.
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))


def subir_servidor():
    import uvicorn
    import app.main as app_main

    # A página pede consulta, instalação e volta de versão ao servidor do app, que repassa para cá.
    app_main.ATUALIZADOR = {'verificar': verificar_pela_pagina, 'instalar': instalar_pela_pagina,
                            'restaurar': restaurar_pela_pagina}
    asgi = app_main.app

    sock = None
    # Depois de atualizar, a versão anterior ainda pode estar soltando a porta: espera até 10 s por ela.
    portas = [PORTA] * (40 if '--aguardar' in sys.argv else 1) + [0]
    for porta in portas:   # porta ocupada (app aberto duas vezes): usa outra
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            if os.name == 'nt':   # ninguém mais escuta nesta porta enquanto o app estiver aberto
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:                 # reabrir logo depois de fechar (porta em TIME_WAIT), como o uvicorn faz
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', porta))
            break
        except OSError:
            sock.close()
            sock = None
            if porta:
                time.sleep(0.25 if len(portas) > 2 else 0)
    if sock is None:
        raise OSError('não consegui abrir uma porta no 127.0.0.1')
    srv = uvicorn.Server(uvicorn.Config(asgi, log_config=None, access_log=False, lifespan='off'))
    threading.Thread(target=srv.run, kwargs={'sockets': [sock]}, daemon=True).start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.05)
    if not srv.started:
        raise RuntimeError('o servidor do app não subiu')
    return srv, f'http://127.0.0.1:{sock.getsockname()[1]}/'


def _trocar(funcao):
    if not TROCANDO_VERSAO.acquire(blocking=False):
        raise RuntimeError('já há uma troca de versão em andamento; espere ela terminar')
    try:
        r = funcao()
    finally:
        TROCANDO_VERSAO.release()
    if r.get('atalhos'):
        atualizar_atalhos(forcar=True)
    if r.get('reiniciar'):   # código novo: o app fecha e abre de novo sozinho, já na outra versão
        threading.Timer(0.8, reiniciar).start()
    return r


def verificar_pela_pagina(forcar=False):
    try:
        return verificar(forcar)
    except Exception as e:   # sem internet etc.: a página decide o que mostrar
        log.warning('consulta de atualização falhou: %s', e)
        return {'atual': versao_instalada(), 'nova': None, 'anterior': versao_anterior(), 'erro': str(e)}


def instalar_pela_pagina():
    return _trocar(instalar_atualizacao)


def restaurar_pela_pagina():
    return _trocar(restaurar)


def reiniciar():
    """Abre o programa de novo (já com o código novo) e fecha este."""
    args = [sys.executable, str(PROGRAMA), '--aguardar', '--reiniciado'] + [a for a in sys.argv[1:] if a == '--navegador']
    log.info('reiniciando para a versão %s', versao_instalada())
    subprocess.Popen(args, cwd=APP, close_fds=True, creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | NO_WINDOW)
    try:
        import webview
        for w in list(webview.windows):
            w.destroy()
    except Exception:
        pass
    os._exit(0)


def em_segundo_plano():
    atualizar_atalhos()
    garantir_atalho_pasta()
    if os.name == 'nt':
        try:
            if preparar_ffmpeg():
                log.info('ffmpeg pronto')
        except Exception:
            log.exception('ffmpeg indisponível por enquanto (os downloads saem num arquivo só, sem juntar vídeo e áudio)')


def pagina_de_erro(detalhe):
    return f'''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><title>{TITULO}</title>
<body style="font:16px system-ui,sans-serif;max-width:640px;margin:48px auto;padding:0 20px;color:#1c1f24">
<h1 style="font-size:1.4rem">O {TITULO} não conseguiu abrir</h1>
<p>Tente fechar e abrir de novo pelo atalho. Se continuar, rode o <b>Instalar {TITULO}</b> outra vez.</p>
<p>Os detalhes ficaram em <b>{html.escape(str(APP / 'logs' / 'downloaderlab.log'))}</b>.</p>
<pre style="white-space:pre-wrap;background:#eef0f3;padding:12px;border-radius:8px;font-size:13px">{html.escape(detalhe)}</pre>
</body></html>'''


# Navegadores que aceitam abrir numa janela nova já maximizada (o Firefox não tem essa opção).
MAXIMIZA = ('msedge.exe', 'chrome.exe', 'brave.exe', 'vivaldi.exe', 'opera.exe')


def comando_maximizado(comando, url):
    m = re.match(r'\s*"([^"]+)"|\s*(\S+)', comando or '')
    exe = m and (m.group(1) or m.group(2))
    nome = PureWindowsPath(exe).name.lower() if exe else ''
    opera = nome == 'launcher.exe' and 'opera' in str(PureWindowsPath(exe).parent).lower()
    if nome not in MAXIMIZA and not opera:
        return None
    return [exe, '--new-window', '--start-maximized', url]


def abrir_maximizado(url):
    """Abre no navegador padrão do Windows, numa janela nova e maximizada. False se não deu."""
    if sys.platform != 'win32':
        return False
    try:
        import winreg
        chave = r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations\http\UserChoice'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, chave) as k:
            progid = winreg.QueryValueEx(k, 'ProgId')[0]
        comando = None
        for raiz, base in ((winreg.HKEY_CURRENT_USER, 'Software\\Classes\\'), (winreg.HKEY_CLASSES_ROOT, '')):
            try:
                with winreg.OpenKey(raiz, base + progid + r'\shell\open\command') as k:
                    comando = winreg.QueryValueEx(k, '')[0]
                break
            except OSError:
                pass
        linha = comando_maximizado(comando, url)
        if not linha or not Path(linha[0]).is_file():
            return False
        subprocess.Popen(linha, close_fds=True, creationflags=NO_WINDOW)
        return True
    except Exception as e:
        log.info('navegador padrão sem janela maximizada (%s); abrindo do jeito comum', e)
        return False


def abrir_no_navegador(url):
    log.info('abrindo no navegador: %s', url)
    if not abrir_maximizado(url):
        webbrowser.open(url)
    threading.Event().wait()   # mantém o servidor no ar enquanto a página estiver aberta


def maximizar_ao_mostrar(janela):
    def mostrar():
        try:
            janela.maximize()
        except Exception:
            log.exception('não consegui maximizar a janela')
    try:
        janela.events.shown += mostrar
    except Exception:
        log.exception('evento shown indisponível')


def main():
    if '--instalar' in sys.argv:
        try:
            instalar_programa()
        except Exception as e:
            log.exception('instalação')
            print(f'\n Erro: {e}', flush=True)
            sys.exit(1)
        return

    navegador = '--navegador' in sys.argv
    try:
        if not VERSAO_ARQ.is_file():
            raise FileNotFoundError(f'os arquivos do app (pasta app) não estão em {APP}')
        preparar_ambiente()
        srv, url = subir_servidor()
        log.info('%s %s em %s (vídeos em %s)', TITULO, versao_instalada(), url, os.environ['DOWNLOAD_DIR'])
    except Exception:
        detalhe = traceback.format_exc()
        log.error(detalhe)
        try:
            import webview
            maximizar_ao_mostrar(webview.create_window(TITULO, html=pagina_de_erro(detalhe), width=760, height=520,
                                                       maximized=True))
            webview.start()
        except Exception:
            erro = APP / 'logs' / 'erro.html'
            erro.write_text(pagina_de_erro(detalhe), encoding='utf-8')
            if not abrir_maximizado(erro.as_uri()):
                webbrowser.open(erro.as_uri())
        return

    threading.Thread(target=em_segundo_plano, daemon=True).start()
    if navegador:
        if '--reiniciado' in sys.argv:   # a aba aberta recarrega sozinha
            return threading.Event().wait()
        return abrir_no_navegador(url)
    try:
        import webview
    except ImportError:
        log.warning('pywebview não instalado; abrindo no navegador')
        return abrir_no_navegador(url)

    try:
        webview.settings['ALLOW_DOWNLOADS'] = True
        janela = webview.create_window(TITULO, url, width=1200, height=850, min_size=(720, 560), maximized=True,
                                       background_color='#f6f7f9', text_select=True)
        maximizar_ao_mostrar(janela)
        DADOS.mkdir(exist_ok=True)
        webview.start(private_mode=False, storage_path=str(DADOS), icon=str(ICONE) if ICONE.is_file() else None)
    except Exception:
        log.exception('a janela não abriu; abrindo no navegador')
        return abrir_no_navegador(url)
    srv.should_exit = True
    # Janela fechada: aproveita para deixar o yt-dlp em dia para a próxima vez (sem janela, em poucos segundos).
    atualizar_ytdlp_se_preciso()


if __name__ == '__main__':
    main()
