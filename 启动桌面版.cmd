@echo off
chcp 65001 >nul
if exist "%~dp0dist\PersonalStorage\PersonalStorage.exe" (
  start "" "%~dp0dist\PersonalStorage\PersonalStorage.exe"
  exit /b 0
)
pushd "%~dp0"
if exist ".venv-desktop\Scripts\pythonw.exe" (
  start "" ".venv-desktop\Scripts\pythonw.exe" -m personalstorage.desktop
) else (
  python -X utf8 -m personalstorage.desktop
)
popd
