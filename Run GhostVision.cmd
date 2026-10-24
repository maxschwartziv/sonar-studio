@echo off
rem Runs GhostVision (separate GPL-3.0-or-later tool) on the JPG tiles in
rem ghostvision_out\tiles\<mode>, then draws its detections (see README).
rem Usage: "Run GhostVision.cmd" [model] [confidence] [iou] [image 1/0] [video 1/0] [mode] [run folder]
rem   run folder: Sonar Studio's ghostvision_out\runs\<slug>_<time>. GhostVision then
rem   reads only <run folder>\tiles, writes <run folder>\pred and prints that run's summary.
rem   model: ghostvision-models/2 (YOLO26), /5 (YOLOv12), /1 (RF-DETR)
rem With no arguments it takes ghostvision_settings.json (Sonar Studio's
rem GhostVision settings) and runs both scales.
cd /d "%~dp0"
set "ASKED=%~1"
set "RUN="
call "%USERPROFILE%\miniforge3\Scripts\activate.bat" ghostvision
if errorlevel 1 (
  echo Could not activate the ghostvision conda env.
  if "%ASKED%"=="" pause
  exit /b 1
)
if "%ASKED%"=="" (
  for /f "tokens=1-5" %%a in ('python ghostvision_feed.py args') do (
    set "MODEL=%%a" & set "CONF=%%b" & set "IOU=%%c" & set "IMG=%%d" & set "VID=%%e"
  )
  set "MODES=honest declared"
) else (
  set "MODEL=%~1"
  set "CONF=%~2"
  set "IOU=%~3"
  set "IMG=%~4"
  set "VID=%~5"
  set "MODES=%~6"
  set "RUN=%~7"
)
if "%MODEL%"=="" set "MODEL=ghostvision-models/2"
if "%CONF%"=="" set "CONF=0.01"
if "%IOU%"=="" set "IOU=0.5"
if "%IMG%"=="" set "IMG=1"
if "%VID%"=="" set "VID=0"
if "%MODES%"=="" set "MODES=honest declared"
set "MODEL_CACHE_DIR=%USERPROFILE%\.ghostvision\models"
set "INFER=import sys; from pingdetect.rf_infer_folder import do_inference as d; d(rf_model=sys.argv[1], in_dir=sys.argv[2], out_dir=sys.argv[3], detect_csv=sys.argv[3]+'/detections.csv', export_image=sys.argv[5]=='1', export_vid=sys.argv[6]=='1', confidence=float(sys.argv[4]), iou_threshold=float(sys.argv[7]))"
if not "%RUN%"=="" (
  python -c "%INFER%" "%MODEL%" "%RUN%\tiles" "%RUN%\pred" "%CONF%" "%IMG%" "%VID%" "%IOU%"
  if errorlevel 1 exit /b 1
  python ghostvision_feed.py summarize-run "%RUN%"
  exit /b
)
for %%M in (%MODES%) do (
  if exist "ghostvision_out\tiles\%%M" (
    python -c "import sys; from pingdetect.rf_infer_folder import do_inference as d; d(rf_model=sys.argv[1], in_dir=sys.argv[2], out_dir=sys.argv[3], detect_csv=sys.argv[3]+'/detections.csv', export_image=sys.argv[5]=='1', export_vid=sys.argv[6]=='1', confidence=float(sys.argv[4]), iou_threshold=float(sys.argv[7]))" "%MODEL%" "ghostvision_out\tiles\%%M" "ghostvision_out\pred\%%M" "%CONF%" "%IMG%" "%VID%" "%IOU%"
  )
)
python ghostvision_feed.py summarize
if "%ASKED%"=="" pause
