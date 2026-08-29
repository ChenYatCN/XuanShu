@echo off
chcp 65001 >nul
title DeimosCN 打包工具

echo ========================================
echo       DeimosCN 自动打包脚本
echo ========================================
echo.

set "ROOT=%~dp0"
cd /d "%ROOT%"

rem Keep PyInstaller away from inaccessible or mismatched user-level packages.
set "PYTHONNOUSERSITE=1"
set "PYTHONUSERBASE=%ROOT%build\python-userbase"

rem Do not inherit Codex/editor DLL directories into the packaged application.
set "PATH=%ROOT%.venv\Scripts;%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0"
set "PYTHONPATH="
set "PYTHONHOME="
set "QT_PLUGIN_PATH="
set "QML2_IMPORT_PATH="
set "QT_QPA_PLATFORM_PLUGIN_PATH="

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

echo [1/6] 检查 PyInstaller...
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
echo [2/6] 清理旧打包缓存...

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

echo [3/6] 开始使用虚拟环境打包...
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
echo [4/6] 检查依赖来源和成品内容...

".venv\Scripts\python.exe" "packaging\verify_bundle.py"

if errorlevel 1 (
    echo.
    echo ========================================
    echo        打包内容校验失败，禁止交付
    echo ========================================
    echo 可能选择了错误的 wizwalker / wizsprinter，
    echo 或缺少 wizlaunch、wizpatch、导航数据等必要文件。
    echo.
    pause
    exit /b 1
)

echo.
echo [5/6] 检查输出文件...

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

echo [6/6] 完成。
echo.

pause
