@echo off
cd /d "%~dp0"
set PY=C:\Users\lq\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=python
echo Fetching weekly supply (inventory / hog / EIA)...
"%PY%" supply_fetch.py
pause
