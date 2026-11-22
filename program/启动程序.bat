@echo off
chcp 65001 >nul
title WHITE ALBUM 2 - 冬马和纱（开发运行）
cd /d "%~dp0"

set "PYEXE=%~dp0build_env\venv\Scripts\python.exe"
if exist "%PYEXE%" goto run

echo.
echo   第一次运行：准备 Python 环境（只需要一次，之后秒开）
echo.

set "LAUNCH="
where py >nul 2>nul
if not errorlevel 1 set "LAUNCH=py -3"
if not defined LAUNCH (
  where python >nul 2>nul
  if not errorlevel 1 set "LAUNCH=python"
)
if not defined LAUNCH goto nopython

%LAUNCH% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 goto oldpython

echo   [1/3] 建虚拟环境 build_env\venv ...
%LAUNCH% -m venv "%~dp0build_env\venv"
if errorlevel 1 goto failed

echo   [2/3] 升级 pip ...
"%PYEXE%" -m pip install --upgrade pip --disable-pip-version-check -q
if errorlevel 1 echo   （pip 升级失败，先继续）

echo   [3/3] 装依赖 Pillow / fonttools / pywebview（第一次要几分钟）...
"%PYEXE%" -m pip install -r "%~dp0requirements.txt" --disable-pip-version-check
if errorlevel 1 goto failed
echo.

:run
"%PYEXE%" "%~dp0tools\run_dev.py" %*
if errorlevel 1 pause
exit /b 0

:oldpython
echo.
echo   找到了 Python，但版本低于 3.10。请装 3.10 以上再双击本文件。
echo.
pause
exit /b 1

:nopython
echo.
echo   没找到 Python。请先装 Python 3.10 以上
echo   （安装时勾选 "Add python.exe to PATH"），再双击本文件。
echo.
pause
exit /b 1

:failed
echo.
echo   环境准备失败（多半是网络或 pip 源）。也可以手动来一次：
echo     python -m venv build_env\venv
echo     build_env\venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
