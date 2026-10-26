@echo off
pushd "%~dp0"
python -X utf8 -m personalstorage %*
set "personalstorage_exit=%errorlevel%"
popd
exit /b %personalstorage_exit%
