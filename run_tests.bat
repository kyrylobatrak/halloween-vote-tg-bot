@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Halloween bot - tests
if not exist .venv\Scripts\python.exe (
  echo Spershu zapusty start.bat: vin stvoryt seredovyshche.
  pause
  exit /b
)
.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
pause
