@echo off
rem Comandos (chcp, powershell, timeout) so das pastas do Windows, nunca um arquivo de mesmo nome deixado na pasta atual
rem (ex.: um chcp.exe falso nos Downloads).
set "NoDefaultCurrentDirectoryInExePath=1"
chcp 65001 >nul
rem Este arquivo e UTF-8 sem BOM: depois do chcp 65001 o cmd le os acentos das mensagens corretamente.
setlocal
cd /d "%~dp0"
rem Aberto de dentro do zip, sem descompactar (o Windows roda numa pasta temporaria): instala sozinho numa pasta
rem "Video Downloader Lab" ao lado do zip (ex.: Downloads) e segue de la (nao precisa de nada do zip: o resto e baixado).
set "AQUI=%~dp0"
set "FORA_TEMP=%AQUI%"
if defined TEMP call set "FORA_TEMP=%%AQUI:%TEMP%=%%"
if /i not "%~1"=="--na-pasta" if /i not "%FORA_TEMP%"=="%AQUI%" goto dentro_do_zip
rem O %TEMP% pode vir com o nome curto do Windows (ex.: JOAOSI~1) e o caminho daqui com o nome inteiro: confere tambem
rem pelo trecho do caminho (7-Zip e WinRAR tambem abrem em pastas temporarias dali).
if /i not "%~1"=="--na-pasta" if /i not "%AQUI:\AppData\Local\Temp\=%"=="%AQUI%" goto dentro_do_zip
rem Solto numa pasta com outras coisas (ex.: "Extrair aqui" nos Downloads): instala numa pasta "Video Downloader Lab" ao lado.
rem (--na-pasta: ja e a copia feita aqui; nunca muda de pasta duas vezes)
rem Pasta que ja se chama "Video Downloader Lab" (ex.: "Extrair tudo" do zip, ou criada pela pessoa): instala nela mesmo.
set "PASTA_APP=Video Downloader Lab"
for %%I in ("%~dp0.") do set "PASTA_NOME=%%~nxI"
set "ITENS=0"
if /i not "%~1"=="--na-pasta" if /i not "%PASTA_NOME%"=="%PASTA_APP%" if not exist "downloaderlab.pyw" if not exist "python\python.exe" (
  for /f "delims=" %%n in ('dir /b /a 2^>nul') do set /a ITENS+=1
)
if %ITENS% GTR 1 (
  if not exist "%PASTA_APP%\" mkdir "%PASTA_APP%"
  copy /y "%~f0" "%PASTA_APP%\%~nx0" >nul
  call "%PASTA_APP%\%~nx0" --na-pasta
  exit /b
)
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
rem atualizacao.json, como nas atualizacoes. Pasta sem o downloaderlab.pyw (ou com um sem o --instalar): baixa um novo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; if ((Test-Path 'downloaderlab.pyw') -and (Select-String -Quiet -SimpleMatch 'def instalar_programa' 'downloaderlab.pyw')) { exit 0 }; Write-Host ' Baixando o Video Downloader Lab...'; $m=(Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Headers @{'Cache-Control'='no-cache'} -Uri ($env:REPO + 'atualizacao.json?t=' + [DateTimeOffset]::Now.ToUnixTimeMilliseconds())).Content | ConvertFrom-Json; $it=@($m.arquivos | Where-Object { $_.caminho -eq 'downloaderlab.pyw' })[0]; if (-not $it -or $it.origem -match '\.\.' -or $it.origem -match '^[/\\]') { throw 'o atualizacao.json nao traz o programa' }; $f='downloaderlab.pyw.novo'; Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri ($env:REPO + $it.origem) -OutFile $f; if ((Get-Item $f).Length -ne $it.tamanho -or (Get-FileHash -Algorithm SHA256 $f).Hash -ne $it.sha256) { Remove-Item $f; throw 'o programa baixado nao confere com o esperado (SHA-256)' }; Move-Item -Force $f 'downloaderlab.pyw'" || goto erro
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
:dentro_do_zip
rem Onde esta o zip: pela janela do Explorador que mostra o conteudo dele; senao, procura o zip com esse nome em
rem Downloads, Area de Trabalho e Documentos. Nao achou ou nao da para gravar ao lado: pasta de programas do usuario.
set "ONDE="
for /f "usebackq delims=" %%d in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='SilentlyContinue'; [Console]::OutputEncoding=[Text.Encoding]::UTF8; $tmp=Split-Path -Leaf $env:AQUI.TrimEnd('\'); $zip=$null; try { foreach ($w in (New-Object -ComObject Shell.Application).Windows()) { $p=$w.Document.Folder.Self.Path; if ($p -and $p -like '*.zip' -and (Test-Path -LiteralPath $p -PathType Leaf) -and $tmp -match ('^Temp\d+_' + [regex]::Escape((Split-Path -Leaf $p)) + '$')) { $zip=$p; break } } } catch {}; if (-not $zip -and $tmp -match '^Temp\d+_(.+\.zip)$') { $nome=$Matches[1]; $dl=$null; try { $dl=(New-Object -ComObject Shell.Application).NameSpace('shell:Downloads').Self.Path } catch {}; foreach ($d in @($dl, (Join-Path $env:USERPROFILE 'Downloads'), [Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('MyDocuments'))) { if ($d) { $c=Join-Path $d $nome; if (Test-Path -LiteralPath $c -PathType Leaf) { $zip=$c; break } } } }; if ($zip) { Split-Path -Parent $zip }"`) do set "ONDE=%%d"
if not defined ONDE goto destino_programas
set "DESTINO=%ONDE%\Video Downloader Lab"
if not exist "%DESTINO%\" mkdir "%DESTINO%" 2>nul
if exist "%DESTINO%\" goto destino_ok
:destino_programas
if not defined LOCALAPPDATA goto zip_fechado
set "DESTINO=%LOCALAPPDATA%\Programs\Video Downloader Lab"
if not exist "%DESTINO%\" mkdir "%DESTINO%"
if not exist "%DESTINO%\" goto zip_fechado
:destino_ok
copy /y "%~f0" "%DESTINO%\%~nx0" >nul
if not exist "%DESTINO%\%~nx0" goto zip_fechado
echo.
echo  Instalando o Video Downloader Lab em:
echo  %DESTINO%
call "%DESTINO%\%~nx0" --na-pasta
exit /b
:zip_fechado
echo.
echo  O Video Downloader Lab ainda está dentro do arquivo .zip.
echo  Clique com o botão direito no arquivo baixado, escolha "Extrair tudo" e, na pasta
echo  "Video Downloader Lab" que aparece, dê dois cliques em "Instalar Video Downloader Lab".
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
