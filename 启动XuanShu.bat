@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo [同步] 校验并同步 wizwalker/mouse_handler.py...
"%~dp0.venv\Scripts\python.exe" -X utf8 "%~dp0packaging\sync_wizwalker_mouse_handler.py"
if errorlevel 1 (
    echo [错误] wizwalker mouse_handler 同步或校验失败，已停止启动。
    pause
    exit /b 1
)
"%~dp0.venv\Scripts\python.exe" -c "import sys,runpy; from pathlib import Path; p=str(Path('.venv/Lib/site-packages').resolve()); sys.path.insert(0,p); runpy.run_path('XuanShu.py',run_name='__main__')"
if errorlevel 1 pause
