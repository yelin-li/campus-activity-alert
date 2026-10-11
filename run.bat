@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [!] 没找到 Python。
  echo     到 python.org 装 Python 3.8 以上，安装时勾选 "Add to PATH"，再双击本文件。
  pause
  exit /b 1
)
if not exist config.json (
  echo [i] 还没有 config.json，先启动配置向导。
  python configure.py
) else (
  python alert.py %*
)
pause
