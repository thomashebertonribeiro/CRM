@echo off
setlocal enabledelayedexpansion
title Prospector — Iniciando...

echo.
echo  ==========================================
echo   PROSPECTOR — Iniciando sistema...
echo  ==========================================
echo.

REM ─── Define variaveis de ambiente ───
set SERPER_KEY=
set OLLAMA_KEY=

REM ─── Le o arquivo .env se existir ───
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        set "_k=%%A"
        set "_v=%%B"
        REM Ignora linhas de comentario
        if not "!_k:~0,1!"=="#" (
            if not "!_k!"=="" (
                set "%%A=%%B"
            )
        )
    )
    echo  [OK] Variaveis carregadas do .env
) else (
    echo  [AVISO] Arquivo .env nao encontrado.
    echo          Configure as chaves em: http://localhost:8088/configuracoes.html
)

REM ─── Valores padrao se nao definidos ───
if "%OLLAMA_BASE%"=="" set OLLAMA_BASE=https://ollama.com/v1
if "%OLLAMA_MODEL%"=="" set OLLAMA_MODEL=glm-5.1
if "%DATA_DIR%"=="" set DATA_DIR=./data
if "%HOST%"=="" set HOST=0.0.0.0
if "%PORT%"=="" set PORT=5000
if "%DEBUG%"=="" set DEBUG=false
if "%ALLOWED_ORIGINS%"=="" set ALLOWED_ORIGINS=http://localhost:8088,http://localhost:5000

REM ─── Inicia o Backend em nova janela ───
echo.
echo  [1/2] Iniciando Backend na porta 5000...
start "Prospector - Backend" cmd /k "cd /d "%~dp0backend" && venv\Scripts\python.exe -m flask --app app.main run --host 0.0.0.0 --port 5000"

REM ─── Aguarda o backend subir ───
timeout /t 4 /nobreak >nul

REM ─── Inicia o Frontend em nova janela ───
echo  [2/2] Iniciando Frontend na porta 8088...
start "Prospector - Frontend" cmd /k "cd /d "%~dp0" && python serve.py"

REM ─── Aguarda o frontend subir ───
timeout /t 2 /nobreak >nul

echo.
echo  ==========================================
echo   Sistema iniciado!
echo.
echo   Prospeccao:    http://localhost:8088
echo   Configuracoes: http://localhost:8088/configuracoes.html
echo  ==========================================
echo.

REM ─── Abre o navegador ───
start "" "http://localhost:8088"

echo  Os servidores estao rodando nas janelas abertas.
echo  Para parar, feche as janelas "Prospector - Backend" e "Prospector - Frontend".
echo.
pause
endlocal
