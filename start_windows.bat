@echo off
setlocal

set "ROOT_DIR=%~dp0"
cd /d "%ROOT_DIR%"

if not exist "main\config.json" (
  if exist "main\config.example.json" (
    echo [STEP] Creating local config from config.example.json
    copy /Y "main\config.example.json" "main\config.json" >nul
  ) else (
    echo [ERROR] main\config.example.json not found.
    pause
    exit /b 1
  )
)

if exist "%UserProfile%\miniconda3\condabin\conda.bat" (
  set "CONDA_BAT=%UserProfile%\miniconda3\condabin\conda.bat"
) else if exist "%UserProfile%\anaconda3\condabin\conda.bat" (
  set "CONDA_BAT=%UserProfile%\anaconda3\condabin\conda.bat"
) else if exist "%ProgramData%\miniconda3\condabin\conda.bat" (
  set "CONDA_BAT=%ProgramData%\miniconda3\condabin\conda.bat"
) else if exist "%ProgramData%\anaconda3\condabin\conda.bat" (
  set "CONDA_BAT=%ProgramData%\anaconda3\condabin\conda.bat"
) else (
  for %%I in (conda.bat) do set "CONDA_BAT=%%~$PATH:I"
)

if not defined CONDA_BAT (
  echo [ERROR] conda.bat not found. Please install Miniconda or Anaconda first.
  pause
  exit /b 1
)

call "%CONDA_BAT%" activate aivtuber
if errorlevel 1 (
  echo [ERROR] Failed to activate conda env: aivtuber
  pause
  exit /b 1
)

python main\start.py --local
pause
