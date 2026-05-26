@echo off
chcp 65001 >nul
title 替换 Deimos 配置并安装字体

echo ========================================
echo 正在替换 Deimos 配置文件并安装字体
echo ========================================
echo.

set "SOURCE_DIR=%~dp0"
set "TARGET_DIR=%APPDATA%\Deimos"
set "FONT_FILE=DreamHanSansCN-W21.ttf"
set "USER_FONT_DIR=%LOCALAPPDATA%\Microsoft\Windows\Fonts"
set "FONT_REG_KEY=HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts"

echo 当前 BAT 所在目录：
echo %SOURCE_DIR%
echo.
echo Deimos 配置目录：
echo %TARGET_DIR%
echo.
echo 用户字体目录：
echo %USER_FONT_DIR%
echo.

if not exist "%TARGET_DIR%" (
    echo 未找到 Deimos 配置目录，正在创建...
    mkdir "%TARGET_DIR%"
)

if not exist "%USER_FONT_DIR%" (
    echo 未找到用户字体目录，正在创建...
    mkdir "%USER_FONT_DIR%"
)

set "BACKUP_DIR=%TARGET_DIR%\backup_%date:~0,4%%date:~5,2%%date:~8,2%_%time:~0,2%%time:~3,2%%time:~6,2%"
set "BACKUP_DIR=%BACKUP_DIR: =0%"

echo 正在备份原配置...
mkdir "%BACKUP_DIR%" >nul 2>nul

if exist "%TARGET_DIR%\settings.json" (
    copy /Y "%TARGET_DIR%\settings.json" "%BACKUP_DIR%\settings.json" >nul
)

if exist "%TARGET_DIR%\default_theme.json" (
    copy /Y "%TARGET_DIR%\default_theme.json" "%BACKUP_DIR%\default_theme.json" >nul
)

echo.
echo 正在替换 Deimos 配置文件...

if exist "%SOURCE_DIR%settings.json" (
    copy /Y "%SOURCE_DIR%settings.json" "%TARGET_DIR%\settings.json" >nul
    echo 已替换 settings.json
) else (
    echo 未找到 settings.json，跳过
)

if exist "%SOURCE_DIR%default_theme.json" (
    copy /Y "%SOURCE_DIR%default_theme.json" "%TARGET_DIR%\default_theme.json" >nul
    echo 已替换 default_theme.json
) else (
    echo 未找到 default_theme.json，跳过
)

echo.
echo 正在安装字体...

if exist "%SOURCE_DIR%%FONT_FILE%" (
    copy /Y "%SOURCE_DIR%%FONT_FILE%" "%USER_FONT_DIR%\%FONT_FILE%" >nul

    reg add "%FONT_REG_KEY%" /v "Dream Han Sans CN W21 (TrueType)" /t REG_SZ /d "%USER_FONT_DIR%\%FONT_FILE%" /f >nul

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$code = '[DllImport(\"user32.dll\", SetLastError=true)] public static extern IntPtr SendMessageTimeout(IntPtr hWnd, uint Msg, UIntPtr wParam, string lParam, uint fuFlags, uint uTimeout, out UIntPtr lpdwResult);';" ^
    "Add-Type -MemberDefinition $code -Name NativeMethods -Namespace Win32;" ^
    "$result = [UIntPtr]::Zero;" ^
    "[Win32.NativeMethods]::SendMessageTimeout([IntPtr]0xffff, 0x001D, [UIntPtr]::Zero, 'Fonts', 2, 5000, [ref]$result) | Out-Null"

    echo 已安装字体：%FONT_FILE%
) else (
    echo 未找到字体文件 %FONT_FILE%，跳过字体安装
)

echo.
echo ========================================
echo 全部完成
echo.
echo 原配置备份位置：
echo %BACKUP_DIR%
echo.
echo 如果字体没有立刻生效，请重启 Deimos 或重启电脑。
echo ========================================
echo.

pause