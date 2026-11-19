@echo off
rem Instalação no Windows: clique duas vezes neste arquivo (ou rode instalar.bat no terminal)
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
where py >nul 2>nul
if %errorlevel%==0 (
  py -3.12 -c "print()" >nul 2>nul && set "PY=py -3.12"
  if not defined PY py -3.11 -c "print()" >nul 2>nul && set "PY=py -3.11"
  if not defined PY set "PY=py -3"
)
if not defined PY set "PY=python"
%PY% instalar.py %*
pause
