@echo off
rem Comandos (chcp, powershell, timeout) so das pastas do Windows, nunca um arquivo de mesmo nome deixado na pasta atual
rem (ex.: um chcp.exe falso nos Downloads).
set "NoDefaultCurrentDirectoryInExePath=1"
chcp 65001 >nul
rem Este arquivo e UTF-8 sem BOM: depois do chcp 65001 o cmd le os acentos das mensagens corretamente.
setlocal
cd /d "%~dp0"
rem Padrao RLA Lab: todos os apps da RLA.dev ficam em C:\rla-lab\<app> (os videos e arquivos da pessoa continuam nas
rem pastas dela). Rodado de qualquer lugar (Downloads, de dentro do zip...), o instalador se copia para la e segue de la.
rem A pasta do app fica so com a pessoa, o Sistema e os Administradores: criada na raiz do C:, ela herdaria "todos os
rem usuarios podem modificar", e outra conta do Windows poderia trocar o programa ou ler o login guardado.
rem Sem permissao para o C:\rla-lab (alguns computadores de empresa): %LOCALAPPDATA%\Programs\rla-lab.
set "PASTA_APP=Video Downloader Lab"
if /i "%~1"=="--na-pasta" goto instalar
set "DESTINO="
for /f "usebackq delims=" %%d in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; $eu=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; $ok=@($eu,'S-1-5-32-544','S-1-5-18','S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'); function Dono($p) { (Get-Acl -LiteralPath $p).GetOwner([Security.Principal.SecurityIdentifier]).Value }; foreach ($raiz in @((Join-Path $env:SystemDrive 'rla-lab'), (Join-Path $env:LOCALAPPDATA 'Programs\rla-lab'))) { try { if (-not (Test-Path -LiteralPath $raiz)) { New-Item -ItemType Directory -Path $raiz | Out-Null }; if ($ok -notcontains (Dono $raiz)) { continue }; $d=Join-Path $raiz $env:PASTA_APP; if (-not (Test-Path -LiteralPath $d)) { New-Item -ItemType Directory -Path $d | Out-Null }; if ($ok -notcontains (Dono $d)) { continue }; & (Join-Path $env:SystemRoot 'System32\icacls.exe') $d /inheritance:r /grant:r ('*' + $eu + ':(OI)(CI)F') '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' /Q | Out-Null; if ($LASTEXITCODE -ne 0) { continue }; Write-Output $d; exit 0 } catch { } }; exit 1"`) do set "DESTINO=%%d"
if not defined DESTINO goto sem_pasta
if /i "%DESTINO%\"=="%~dp0" goto instalar
copy /y "%~f0" "%DESTINO%\%~nx0" >nul
if not exist "%DESTINO%\%~nx0" goto sem_pasta
echo.
echo  Instalando o Video Downloader Lab em:
echo  %DESTINO%
call "%DESTINO%\%~nx0" --na-pasta
exit /b
:instalar
title Video Downloader Lab - Instalação
echo.
echo  Video Downloader Lab - Baixe vídeos de várias plataformas direto no seu computador
echo.
echo  A instalação baixa os componentes do programa (cerca de 230 MB, só na primeira vez).
echo  Para baixar vídeos, o Video Downloader Lab também precisa de internet.
echo.
set "REPO=https://raw.githubusercontent.com/rla-devv/video-downloader-lab-versoes/main/"
if defined DOWNLOADERLAB_REPO_VERSOES set "REPO=%DOWNLOADERLAB_REPO_VERSOES%"
rem Confere a internet antes de baixar qualquer coisa: sem ela, avisa na hora em vez de parar no meio.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Method Head -TimeoutSec 20 -Uri ($env:REPO + 'atualizacao.json') | Out-Null } catch { exit 1 }" || goto sem_internet
echo  Instalando o Video Downloader Lab...
echo.
rem Python so do Video Downloader Lab: o portatil oficial do python.org, em python\ (nao mexe no resto do computador).
set "PY_URL=https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip"
set "PY_SHA=a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310"
set "PYEXE=python\python.exe"
set "PYW=python\pythonw.exe"
if not exist "%PYEXE%" (
  echo  Baixando o Python do Video Downloader Lab ^(cerca de 13 MB, só na primeira vez^)...
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $z=Join-Path $env:TEMP 'downloaderlab-python.zip'; Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri $env:PY_URL -OutFile $z; if ((Get-FileHash -Algorithm SHA256 $z).Hash -ne $env:PY_SHA) { Remove-Item $z; throw 'o arquivo baixado nao confere com o esperado (SHA-256)' }; Expand-Archive -Force $z 'python'; Remove-Item $z; $pth=(Get-ChildItem 'python\python*._pth')[0].FullName; (Get-Content $pth) -replace '^#\s*import site','import site' | Set-Content -Encoding ascii $pth" || goto erro
)
rem O pacote so traz este arquivo: o programa vem do repositorio de versoes, conferido (tamanho e SHA-256) pelo
rem atualizacao.json, como nas atualizacoes. Um downloaderlab.pyw que ja esteja na pasta so e usado se for igual ao
rem publicado (um arquivo deixado ali por outro programa nunca e executado); diferente ou faltando: baixa o publicado.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; $m=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Headers @{'Cache-Control'='no-cache'} -Uri ($env:REPO + 'atualizacao.json?t=' + [DateTimeOffset]::Now.ToUnixTimeMilliseconds())).Content | ConvertFrom-Json; $it=@($m.arquivos | Where-Object { $_.caminho -eq 'downloaderlab.pyw' })[0]; if (-not $it -or $it.origem -match '\.\.' -or $it.origem -match '^[/\\]' -or $it.origem -match ':') { throw 'o atualizacao.json nao traz o programa' }; if ((Test-Path 'downloaderlab.pyw') -and (Get-Item 'downloaderlab.pyw').Length -eq $it.tamanho -and (Get-FileHash -Algorithm SHA256 'downloaderlab.pyw').Hash -eq $it.sha256) { exit 0 }; Write-Host ' Baixando o Video Downloader Lab...'; $f='downloaderlab.pyw.novo'; Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri ($env:REPO + $it.origem) -OutFile $f; if ((Get-Item $f).Length -ne $it.tamanho -or (Get-FileHash -Algorithm SHA256 $f).Hash -ne $it.sha256) { Remove-Item $f; throw 'o programa baixado nao confere com o esperado (SHA-256)' }; Move-Item -Force $f 'downloaderlab.pyw'" || goto erro
"%PYEXE%" downloaderlab.pyw --instalar || goto erro
echo.
echo  Pronto! O Video Downloader Lab está abrindo.
echo  Das próximas vezes, abra pelo atalho "Video Downloader Lab" na Área de Trabalho ou no Menu Iniciar.
echo.
start "" "%~dp0%PYW%" "%~dp0downloaderlab.pyw"
timeout /t 6 >nul
exit /b 0
:sem_internet
echo  Sem conexão com a internet.
echo  A instalação precisa de internet para baixar os componentes do programa (só desta vez).
echo  Conecte o computador à internet e rode este arquivo de novo.
echo.
pause
exit /b 1
:sem_pasta
echo.
echo  Não consegui criar a pasta do Video Downloader Lab em C:\rla-lab nem na pasta de programas do seu usuário.
echo  Confira se há espaço no disco e rode este arquivo de novo. Se o computador for de empresa, peça ajuda ao suporte.
echo.
pause
exit /b 1
:erro
echo.
echo  A instalação não terminou. Veja a mensagem acima.
echo  Confira a internet e rode este arquivo de novo.
echo.
pause
exit /b 1
