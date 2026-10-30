@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"
title Resume Doctor Demo

echo.
echo  ============================================================
echo   简历诊断 Agent（Resume Doctor Demo 2.0）
echo  ============================================================
echo.

set "PYEXE=%~dp0runtime\python\python.exe"
if exist "%PYEXE%" goto :has_py

echo  [内置运行时缺失] runtime\python\python.exe 不存在，改用系统 Python…
set "PYEXE="
py -3 -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>nul
if not errorlevel 1 set "PYEXE=py -3"
if not defined PYEXE (
  for %%V in (313 312 311 310 39 38) do (
    if not defined PYEXE if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
      "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" -c "import sys;sys.exit(0 if sys.version_info>=(3,8) else 1)" >nul 2>nul
      if not errorlevel 1 set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe"
    )
  )
)
if not defined PYEXE goto :nopython
goto :run

:has_py
for /f "delims=" %%v in ('"%PYEXE%" -c "import sys;print(sys.version.split()[0])" 2^>nul') do set "PYVER=%%v"
echo  内置 Python 运行时：%PYVER%（无需安装任何环境）

:run
if exist "%~dp0app\config.json" goto :key_ok
echo.
echo  [提示] 没有 app\config.json：将以「离线演示模式」运行。
echo         功能全部可用（追问/依据/校准/看板），只是评分改由本地规则引擎完成。
echo         想用真实大模型：在 app 目录下把 config.example.json 复制成 config.json，填入 api_key。
echo.
goto :start

:key_ok
echo  配置文件：app\config.json（已加载，大模型可用）
goto :start

:start
echo.
echo  正在启动服务，浏览器会自动打开 http://127.0.0.1:8765
echo  关闭这个窗口就是停止服务。
echo  演示脚本见「演示步骤.txt」，完整说明见「README.md」。
echo.
"%PYEXE%" "%~dp0app\app.py" --port 8765 --open
set "RC=%errorlevel%"
echo.
if not "%RC%"=="0" (
  echo  [服务异常退出] 退出码 %RC%
  echo    · 端口被占用：已有一个窗口在跑，直接访问 http://127.0.0.1:8765 即可。
  echo    · 其它报错：把上面几行原文发出来定位。
  echo.
)
pause
exit /b 0

:nopython
echo  [找不到可用的 Python]
echo   本应使用项目自带的 runtime\python\python.exe，但它不见了。
echo   请重新解压整个项目文件夹（不要只拷 app 目录），或安装 Python 3.8+ 后重试。
echo.
pause
exit /b 1
