@echo off
REM Builds LearnerOnPC on Windows: a Python environment, the tests, the app folder and a zip to share.
REM Needs Python 3.10-3.12 (64-bit) and an internet connection the first time. Takes about 5-15 minutes.
cd /d "%~dp0"
title Building LearnerOnPC
set LOG=build_log.txt
echo ===== build started %date% %time% ===== > %LOG%
if not exist .buildenv\Scripts\python.exe (
  echo Creating the Python environment... >> %LOG%
  py -3.12 -m venv .buildenv >> %LOG% 2>&1 || python -m venv .buildenv >> %LOG% 2>&1
)
if not exist .buildenv\Scripts\python.exe (
  echo FAILED: could not create a Python environment - is Python 3.10-3.12 installed? >> %LOG%
  goto :end
)
echo Installing packages... >> %LOG%
.buildenv\Scripts\python.exe -m pip install -q --upgrade pip >> %LOG% 2>&1
.buildenv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller >> %LOG% 2>&1 || (echo FAILED: package install >> %LOG% & goto :end)
echo Running the tests... >> %LOG%
.buildenv\Scripts\python.exe -u main.py --selftest >> %LOG% 2>&1 || (echo FAILED: self-test >> %LOG% & goto :end)
.buildenv\Scripts\python.exe -u tests\test_demo.py >> %LOG% 2>&1 || (echo FAILED: window tests >> %LOG% & goto :end)
echo Building the app folder... >> %LOG%
.buildenv\Scripts\python.exe -m PyInstaller --noconfirm LearnerOnPC.spec >> %LOG% 2>&1 || (echo FAILED: PyInstaller >> %LOG% & goto :end)
start "" /wait dist\LearnerOnPC\LearnerOnPC.exe --selftest
findstr /c:"SELFTEST OK" "%LOCALAPPDATA%\LearnerOnPC\selftest_result.txt" >nul || (echo FAILED: the built app's self-test >> %LOG% & goto :end)
echo Packing the zip... >> %LOG%
powershell -NoProfile -Command "Compress-Archive -Path 'dist\LearnerOnPC' -DestinationPath 'LearnerOnPC-windows.zip' -Force" >> %LOG% 2>&1 || (echo FAILED: zip >> %LOG% & goto :end)
echo DONE - LearnerOnPC-windows.zip is ready >> %LOG%
:end
echo ===== build ended %date% %time% ===== >> %LOG%
type %LOG%
pause
