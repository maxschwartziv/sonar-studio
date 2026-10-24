@echo off
rem Upgrades RockMapper with PINGInstaller (see README): from Miniforge's base env,
rem reinstalls pinginstaller, then updates the existing rockmapper conda env in place.
rem Downloads packages from the internet. Also updates the base env.
rem Usage: "Upgrade RockMapper.cmd" [nopause]
cd /d "%~dp0"
set "NOPAUSE=%~1"
set "CODE=import importlib.metadata as m; print(m.version('rockmapper'))"
call "%USERPROFILE%\miniforge3\Scripts\activate.bat" base
if errorlevel 1 (
  echo Could not activate Miniforge's base env.
  if not "%NOPAUSE%"=="nopause" pause
  exit /b 1
)
for /f "delims=" %%v in ('conda run -n rockmapper python -c "%CODE%" 2^>nul') do set "BEFORE=%%v"
echo RockMapper before: %BEFORE%
if "%NOPAUSE%"=="" (
  echo This changes the rockmapper conda env and the base env and downloads packages.
  choice /m "Continue"
  if errorlevel 2 exit /b 1
)
pip install --force-reinstall pinginstaller
if errorlevel 1 goto failed
python -m pinginstaller rockmapper
if errorlevel 1 goto failed
for /f "delims=" %%v in ('conda run -n rockmapper python -c "%CODE%" 2^>nul') do set "AFTER=%%v"
echo RockMapper before: %BEFORE%
echo RockMapper after:  %AFTER%
if not "%NOPAUSE%"=="nopause" pause
exit /b 0
:failed
echo Upgrade failed.
if not "%NOPAUSE%"=="nopause" pause
exit /b 1
