@echo off
rem Runs RockMapper on the GeoTIFFs in rockmapper_out\input (see README).
cd /d "%~dp0"
call "%USERPROFILE%\miniforge3\Scripts\activate.bat" rockmapper
if errorlevel 1 (
  echo Could not activate the rockmapper conda env.
  pause
  exit /b 1
)
python rockmapper_feed.py run %*
if "%~1"=="" pause
