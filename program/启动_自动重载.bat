@echo off
chcp 65001 >nul
title WHITE ALBUM 2 - 冬马和纱（开发·自动重载）
cd /d "%~dp0"

rem 和「启动程序.bat」同一套环境准备，只是多带一个 --watch：
rem 改完 src/ 里的文件，会自动重新同步（含 ?v= 版本号）并重启程序。
call "%~dp0启动程序.bat" %* --watch
exit /b %errorlevel%
