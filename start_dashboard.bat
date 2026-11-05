@echo off
cd /d "%~dp0"
set PY=C:\Users\lq\AppData\Local\Programs\Python\Python313\python.exe
if not exist "%PY%" set PY=python
echo Starting futures dashboard at http://127.0.0.1:5050
"%PY%" app.py
pause
