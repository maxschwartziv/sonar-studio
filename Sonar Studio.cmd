@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "" pythonw sonar_studio.py
) else (
  python sonar_studio.py
)
