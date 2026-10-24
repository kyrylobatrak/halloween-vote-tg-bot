@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Halloween bot
if not exist .venv\Scripts\python.exe (
  echo Pershyi zapusk: stvoryuyu seredovyshche, ce zaime khvylynu...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo [!] Ne znaishov Python. Vstanovy z python.org i postav galochku "Add to PATH".
  pause
  exit /b
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
.venv\Scripts\python.exe bot.py
pause
