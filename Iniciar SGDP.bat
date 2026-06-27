@echo off

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERRO: Python nao encontrado.
    echo  Instale em https://www.python.org/downloads/
    echo  e marque "Add Python to PATH" durante a instalacao.
    echo.
    pause
    exit /b 1
)

echo.
echo  ================================================
echo   SGDP -- Gestao de Documentos da Procuradoria
echo   http://localhost:3001
echo  ================================================
echo.
echo  Login inicial: usuario=admin  senha=sgdp2024
echo  Altere a senha apos o primeiro acesso.
echo.
echo  Pressione Ctrl+C para encerrar o servidor.
echo.

python "%~dp0server.py"
