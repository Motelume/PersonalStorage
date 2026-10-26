@echo off
chcp 65001 >nul
pushd "%~dp0"
python -X utf8 -m personalstorage serve
set "personalstorage_exit=%errorlevel%"
popd
if not "%personalstorage_exit%"=="0" pause
exit /b %personalstorage_exit%
