@echo off
chcp 65001 >nul
title DeimosCN 打包工具

echo ========================================
echo       DeimosCN 自动打包脚本
echo ========================================
echo.

set "ROOT=%~dp0"
cd /d "%ROOT%"

echo 当前项目目录：
echo %ROOT%
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境：
    echo %ROOT%.venv\Scripts\python.exe
    echo.
    echo 请先确认项目目录下有 .venv 文件夹。
    pause
    exit /b 1
)

if not exist "DeimosCN.py" (
    echo [错误] 当前目录未找到 DeimosCN.py
    echo 请把本 bat 放在 DeimosCN.py 同级目录。
    pause
    exit /b 1
)

if not exist "DeimosCN.spec" (
    echo [错误] 当前目录未找到 DeimosCN.spec
    echo 请确认 spec 文件名是否为 DeimosCN.spec。
    pause
    exit /b 1
)

echo [1/5] 检查 PyInstaller...
".venv\Scripts\python.exe" -m pip show pyinstaller >nul 2>nul

if errorlevel 1 (
    echo 未检测到 PyInstaller，正在安装...
    ".venv\Scripts\python.exe" -m pip install pyinstaller
    if errorlevel 1 (
        echo.
        echo [错误] PyInstaller 安装失败。
        pause
        exit /b 1
    )
) else (
    echo PyInstaller 已安装。
)

echo.
echo [2/5] 清理旧打包缓存...

if exist "build" (
    rmdir /s /q "build"
)

if exist "dist" (
    rmdir /s /q "dist"
)

if exist "__pycache__" (
    rmdir /s /q "__pycache__"
)

echo 清理完成。
echo.

echo [3/5] 开始使用虚拟环境打包...
echo.

".venv\Scripts\python.exe" -m PyInstaller "DeimosCN.spec" --clean -y

if errorlevel 1 (
    echo.
    echo ========================================
    echo              打包失败
    echo ========================================
    echo 请查看上方报错信息。
    pause
    exit /b 1
)

echo.
echo [4/5] 检查输出文件...

if exist "dist\DeimosCN.exe" (
    echo.
    echo ========================================
    echo              打包成功
    echo ========================================
    echo 输出文件：
    echo %ROOT%dist\DeimosCN.exe
    echo.
) else (
    echo.
    echo [警告] 打包命令执行完成，但没有找到：
    echo %ROOT%dist\DeimosCN.exe
    echo.
    echo 请检查 spec 文件里的 name 是否为 DeimosCN。
    pause
    exit /b 1
)

echo [5/5] 完成。
echo.

pause
