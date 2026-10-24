@echo off
chcp 65001 >nul
cd /d "%~dp0"
where git >nul 2>nul || (echo [!] Git ne vstanovleno: https://git-scm.com/download/win & pause & exit /b)
if exist .git (echo Repozytorii vzhe isnuie. & git log --oneline -5 & pause & exit /b)
git init -b main
git add -A
git commit -m "Halloween Party: bot konkursu kostiumiv"
echo.
echo Gotovo! Repozytorii stvoreno.
git log --oneline
pause
