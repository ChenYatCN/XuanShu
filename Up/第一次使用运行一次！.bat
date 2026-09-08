@echo off
setlocal EnableExtensions
chcp 65001 >nul
title 替换 XuanShu 配置并安装字体

echo ========================================
echo 正在替换 XuanShu 配置文件并安装字体
echo ========================================
echo.

set "SOURCE_DIR=%~dp0"
set "TARGET_DIR=%APPDATA%\XuanShu"

set "FONT_FILE=DreamHanSansCN-W21.ttf"
set "FONT_NAME=Dream Han Sans CN W21"
set "FONT_REG_NAME=Dream Han Sans CN W21 (TrueType)"
set "USER_FONT_DIR=%LOCALAPPDATA%\Microsoft\Windows\Fonts"
set "FONT_TARGET=%USER_FONT_DIR%\%FONT_FILE%"
set "FONT_REG_KEY=HKCU\Software\Microsoft\Windows NT\CurrentVersion\Fonts"

echo 当前 BAT 所在目录：
echo %SOURCE_DIR%
echo.
echo XuanShu 配置目录：
echo %TARGET_DIR%
echo.
echo 用户字体目录：
echo %USER_FONT_DIR%
echo.

echo 正在关闭 XuanShu...
taskkill /F /IM XuanShu.exe >nul 2>nul

if not exist "%TARGET_DIR%" (
    echo 未找到 XuanShu 配置目录，正在创建...
    mkdir "%TARGET_DIR%"
)

if not exist "%USER_FONT_DIR%" (
    echo 未找到用户字体目录，正在创建...
    mkdir "%USER_FONT_DIR%"
)

set "BACKUP_STAMP=%date%_%time%"
set "BACKUP_STAMP=%BACKUP_STAMP:/=%"
set "BACKUP_STAMP=%BACKUP_STAMP::=%"
set "BACKUP_STAMP=%BACKUP_STAMP:.=%"
set "BACKUP_STAMP=%BACKUP_STAMP: =0%"

set "BACKUP_DIR=%TARGET_DIR%\backup_%BACKUP_STAMP%"

echo.
echo 正在备份原配置...
mkdir "%BACKUP_DIR%" >nul 2>nul

if exist "%TARGET_DIR%\settings.json" (
    copy /Y "%TARGET_DIR%\settings.json" "%BACKUP_DIR%\settings.json" >nul
)

if exist "%TARGET_DIR%\default_theme.json" (
    copy /Y "%TARGET_DIR%\default_theme.json" "%BACKUP_DIR%\default_theme.json" >nul
)

echo.
echo 正在替换 XuanShu 配置文件...

if exist "%SOURCE_DIR%settings.json" (
    copy /Y "%SOURCE_DIR%settings.json" "%TARGET_DIR%\settings.json" >nul
    if errorlevel 1 (
        echo settings.json 替换失败
    ) else (
        echo 已替换 settings.json
    )
) else (
    echo 未找到 settings.json，跳过
)

if exist "%SOURCE_DIR%default_theme.json" (
    copy /Y "%SOURCE_DIR%default_theme.json" "%TARGET_DIR%\default_theme.json" >nul
    if errorlevel 1 (
        echo default_theme.json 替换失败
    ) else (
        echo 已替换 default_theme.json
    )
) else (
    echo 未找到 default_theme.json，跳过
)

echo.
echo 正在安装字体...

if not exist "%SOURCE_DIR%%FONT_FILE%" (
    echo 未找到字体文件：
    echo %SOURCE_DIR%%FONT_FILE%
    echo 跳过字体安装
    goto FINISH
)

echo 正在复制字体文件...

copy /Y "%SOURCE_DIR%%FONT_FILE%" "%FONT_TARGET%" >nul 2>nul

if errorlevel 1 (
    echo.
    echo 字体复制失败。
    echo 可能原因：字体文件正在被系统或程序占用。
    echo 如果字体已经存在，将继续写入注册表。
    echo.
) else (
    echo 已复制字体文件到：
    echo %FONT_TARGET%
)

echo.
echo 正在写入字体注册表...

reg delete "%FONT_REG_KEY%" /v "%FONT_REG_NAME%" /f >nul 2>nul

reg add "%FONT_REG_KEY%" /v "%FONT_REG_NAME%" /t REG_SZ /d "%FONT_TARGET%" /f >nul

if errorlevel 1 (
    echo 字体注册表写入失败
) else (
    echo 字体注册表写入成功
)

echo.
echo 已安装字体：%FONT_NAME%
echo.
echo 注意：
echo Windows 字体缓存可能不会立刻刷新。
echo 请重新打开 XuanShu。
echo 如果字体仍然没有生效，请重启电脑后再打开 XuanShu。

:FINISH
echo.
echo ========================================
echo 全部完成
echo.
echo 原配置备份位置：
echo %BACKUP_DIR%
echo.
echo 请重新打开 XuanShu。
echo 如果字体仍然没有生效，请重启电脑后再打开 XuanShu。
echo ========================================
echo.

pause
endlocal