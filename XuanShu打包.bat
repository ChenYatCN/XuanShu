@echo off
chcp 65001 >nul
title XuanShu 打包工具

echo ========================================
echo       XuanShu 自动打包脚本
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

if not exist "XuanShu.py" (
    echo [错误] 当前目录未找到 XuanShu.py
    echo 请把本 bat 放在 XuanShu.py 同级目录。
    pause
    exit /b 1
)

if not exist "XuanShu.spec" (
    echo [错误] 当前目录未找到 XuanShu.spec
    echo 请确认 spec 文件名是否为 XuanShu.spec。
    pause
    exit /b 1
)

echo [同步] 校验并同步 wizwalker/mouse_handler.py...
".venv\Scripts\python.exe" -X utf8 "packaging\sync_wizwalker_mouse_handler.py"
if errorlevel 1 (
    echo.
    echo [错误] wizwalker mouse_handler 同步或校验失败，已停止打包。
    pause
    exit /b 1
)

echo [1/8] 检查新版战斗后端及双层附魔语法...
".venv\Scripts\python.exe" -X utf8 "packaging\verify_combat_backend.py"
if errorlevel 1 (
    echo.
    echo [错误] 战斗后端检测未通过，已停止打包。旧成品和缓存未清理。
    pause
    exit /b 1
)

echo.
echo [2/8] 检查 PyInstaller...
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
echo [3/8] 清理旧打包缓存...

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

echo [4/8] 开始使用虚拟环境打包...
echo.

".venv\Scripts\python.exe" -m PyInstaller "XuanShu.spec" --clean -y

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
echo [5/8] 检查依赖来源、成品内容和编译后的战斗后端...

".venv\Scripts\python.exe" -X utf8 "packaging\verify_bundle.py"

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
echo [6/8] 检查输出文件...

if exist "dist\XuanShu.exe" (
    echo 输出文件存在，等待数字签名。
) else (
    echo.
    echo [警告] 打包命令执行完成，但没有找到：
    echo %ROOT%dist\XuanShu.exe
    echo.
    echo 请检查 spec 文件里的 name 是否为 XuanShu。
    pause
    exit /b 1
)

echo.
echo [7/8] 添加并验证 ChenYatCN Authenticode 数字签名...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $exe=(Resolve-Path -LiteralPath 'dist\XuanShu.exe').Path; $base='C:\Program Files (x86)\Windows Kits\10\bin'; $signtool=$null; if (Test-Path -LiteralPath $base) { $signtool=Get-ChildItem -LiteralPath $base -Recurse -Filter 'signtool.exe' -File -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '\\x64\\signtool\.exe$' -and $_.Directory.Parent.Name -match '^\d+(\.\d+)+$' } | Sort-Object { [version]$_.Directory.Parent.Name } -Descending | Select-Object -First 1 }; if (-not $signtool) { Write-Host '[签名失败] 未找到 Windows SDK signtool.exe。'; Write-Host '请安装 Windows 10/11 SDK。'; exit 1 }; $cert=$null; $store=New-Object System.Security.Cryptography.X509Certificates.X509Store('My','CurrentUser'); $store.Open('ReadOnly'); foreach ($candidate in $store.Certificates) { if ($candidate.Subject -match 'CN=ChenYatCN(,|$)' -and $candidate.HasPrivateKey -and $candidate.NotAfter -gt (Get-Date)) { $codeSigning=$false; foreach ($ext in $candidate.Extensions) { if ($ext.Oid.Value -eq '2.5.29.37') { foreach ($oid in $ext.EnhancedKeyUsages) { if ($oid.Value -eq '1.3.6.1.5.5.7.3.3') { $codeSigning=$true } } } }; if ($codeSigning -and ($null -eq $cert -or $candidate.NotAfter -gt $cert.NotAfter)) { $cert=$candidate } } }; $store.Close(); if (-not $cert) { Write-Host '[签名失败] 未找到可用的 ChenYatCN 代码签名证书。'; Write-Host '请确认 CN=ChenYatCN 的 Code Signing 证书已安装到 CurrentUser\My 且包含私钥。'; exit 1 }; Write-Host ('[签名] 证书：CN=ChenYatCN'); Write-Host ('[签名] Subject：' + $cert.Subject); Write-Host ('[签名] Thumbprint：' + $cert.Thumbprint); Write-Host ('[签名] SignTool：' + $signtool.FullName); Write-Host ('[签名] 目标：dist\XuanShu.exe'); & $signtool.FullName sign /sha1 $cert.Thumbprint /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $exe; if ($LASTEXITCODE -ne 0) { Write-Host ('[签名失败] signtool sign 返回 ' + $LASTEXITCODE); exit $LASTEXITCODE }; $ErrorActionPreference='Continue'; $verifyOutput = (& $signtool.FullName verify /pa /v $exe 2>&1 | Out-String); $verifyCode=$LASTEXITCODE; if ($verifyCode -ne 0) { if ($verifyOutput -match 'not trusted|terminated in a root|A certificate chain processed') { Write-Host '[签名提示] 自签名证书未加入本机信任链；签名已写入，继续检查签名主体。' } else { Write-Host $verifyOutput; Write-Host '[签名失败] XuanShu.exe Authenticode 验证失败。'; exit $verifyCode } }; $signer=[System.Security.Cryptography.X509Certificates.X509Certificate]::CreateFromSignedFile($exe); Write-Host ('[签名信息] Signer：' + $signer.Subject); Write-Host ('[签名信息] CertHash：' + $signer.GetCertHashString()); if ($verifyOutput -match 'timestamped') { Write-Host '[签名信息] Timestamp：signtool verified timestamp' }; if ($signer.Subject -notmatch 'CN=ChenYatCN') { Write-Host '[签名失败] 签名证书主体不是 ChenYatCN。'; exit 1 }; Write-Host '[签名通过] XuanShu.exe 已成功签名并验证。'; exit 0"

if errorlevel 1 (
    echo.
    echo ========================================
    echo        数字签名失败，禁止交付
    echo ========================================
    echo 未生成带有效 ChenYatCN 签名的 XuanShu.exe。
    pause
    exit /b 1
)

echo.
echo ========================================
echo        玄枢 XuanShu 打包完成
echo 数字签名：ChenYatCN
echo 成品：dist\XuanShu.exe
echo ========================================
echo.
echo [8/8] 完成。
echo.

pause
