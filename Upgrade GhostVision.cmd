@echo off
rem Upgrades GhostVision with PINGInstaller (see README): from Miniforge's base env,
rem reinstalls pinginstaller, then updates the existing ghostvision conda env in place.
rem Downloads packages from the internet. Also updates the base env.
rem Usage: "Upgrade GhostVision.cmd" [nopause] [withinference], in any order
rem   withinference: then keep inference-gpu at 1.x: pycuda from conda-forge (pip would
rem   need Microsoft C++ Build Tools to compile it), upgrade inference-gpu, then reinstall
rem   inference-sdk, inference-gpu and inference-models (removing 0.64 deletes their files).
cd /d "%~dp0"
set "NOPAUSE="
set "WITHINF="
for %%A in (%*) do (
  if /i "%%~A"=="nopause" set "NOPAUSE=nopause"
  if /i "%%~A"=="withinference" set "WITHINF=withinference"
)
set "CODE=import importlib.metadata as m; print(m.version('ghostvision'))"
call "%USERPROFILE%\miniforge3\Scripts\activate.bat" base
if errorlevel 1 (
  echo Could not activate Miniforge's base env.
  if not "%NOPAUSE%"=="nopause" pause
  exit /b 1
)
for /f "delims=" %%v in ('conda run -n ghostvision python -c "%CODE%" 2^>nul') do set "BEFORE=%%v"
echo GhostVision before: %BEFORE%
if "%NOPAUSE%"=="" (
  echo This changes the ghostvision conda env and the base env and downloads packages.
  choice /m "Continue"
  if errorlevel 2 exit /b 1
)
pip install --force-reinstall pinginstaller
if errorlevel 1 goto failed
python -m pinginstaller ghostvision-gpu
if errorlevel 1 goto failed
if "%WITHINF%"=="withinference" (
  echo Keeping inference-gpu at 1.x in the ghostvision env.
  call "%USERPROFILE%\miniforge3\Library\bin\mamba.exe" install -y -n ghostvision -c conda-forge pycuda
  if errorlevel 1 goto failed
  conda run -n ghostvision python -m pip install --upgrade inference-gpu
  if errorlevel 1 goto failed
  for /f "tokens=2 delims==" %%v in ('conda run -n ghostvision python -m pip freeze ^| findstr /b /i "inference-gpu=="') do set "INFV=%%v"
  conda run -n ghostvision python -m pip install --force-reinstall --no-deps inference-sdk==%INFV% inference-gpu==%INFV% inference-models
  if errorlevel 1 goto failed
)
for /f "delims=" %%v in ('conda run -n ghostvision python -c "%CODE%" 2^>nul') do set "AFTER=%%v"
echo GhostVision before: %BEFORE%
echo GhostVision after:  %AFTER%
if not "%NOPAUSE%"=="nopause" pause
exit /b 0
:failed
echo Upgrade failed.
if not "%NOPAUSE%"=="nopause" pause
exit /b 1
