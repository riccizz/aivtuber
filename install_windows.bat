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

echo [STEP] Accepting Anaconda channel Terms of Service
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main
if errorlevel 1 goto :fail
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r
if errorlevel 1 goto :fail
call "%CONDA_BAT%" tos accept --override-channels --channel https://repo.anaconda.com/pkgs/msys2
if errorlevel 1 goto :fail

echo [STEP] Creating or updating conda env: aivtuber
call "%CONDA_BAT%" create -n aivtuber python=3.10 -y
if errorlevel 1 goto :fail

echo [STEP] Activating env: aivtuber
call "%CONDA_BAT%" activate aivtuber
if errorlevel 1 goto :fail

echo [STEP] Upgrading pip
python -m pip install --upgrade pip
if errorlevel 1 goto :fail

echo [STEP] Installing PyTorch CUDA 12.8 wheels
python -m pip install torch==2.9.1 torchaudio==2.9.1 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto :fail

echo [STEP] Installing project requirements
python -m pip install -r requirements.txt
if errorlevel 1 goto :fail

echo.
echo [DONE] Install completed.
echo Use start_windows.bat to run the app.
pause
exit /b 0

:fail
echo.
echo [ERROR] Install failed.
pause
exit /b 1
