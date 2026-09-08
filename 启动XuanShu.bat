@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" -c "import sys,runpy; from pathlib import Path; p=str(Path('.venv/Lib/site-packages').resolve()); sys.path.insert(0,p); runpy.run_path('XuanShu.py',run_name='__main__')"
if errorlevel 1 pause
