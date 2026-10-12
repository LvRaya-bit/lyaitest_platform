@echo off
chcp 936 >nul
setlocal
cd /d "%~dp0"

set "PY=%~dp0venv\Scripts\python.exe"
set "BACKEND_PORT=8001"
set "FRONTEND_PORT=8501"

if not exist "%PY%" goto no_venv

echo.
echo ============================================================
echo   LYAITEST 本地启动
echo   后端 FastAPI   : http://127.0.0.1:%BACKEND_PORT%
echo   前端 Streamlit : http://localhost:%FRONTEND_PORT%
echo ============================================================
echo.

echo [1/3] 启动后端...
start "LYAITEST 后端" cmd /k ""%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%"

echo [2/3] 启动前端...
start "LYAITEST 前端" cmd /k ""%PY%" -m streamlit run app_frontend.py --server.port %FRONTEND_PORT%"

echo [3/3] 等待后端就绪（最多 40 秒）...
set /a tries=0

:wait_backend
ping -n 2 127.0.0.1 >nul
set /a tries+=1
powershell -NoProfile -Command "try{ if((Invoke-WebRequest -Uri 'http://127.0.0.1:%BACKEND_PORT%/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200){exit 0} }catch{}; exit 1" >nul 2>&1
if not errorlevel 1 goto backend_ok
if %tries% LSS 40 goto wait_backend
echo       后端 40 秒内未就绪，仍会打开前端页面。
echo       请查看「LYAITEST 后端」窗口里的报错。
goto open_front

:backend_ok
echo       后端已就绪。

:open_front
echo.
echo 打开前端页面...
start "" "http://localhost:%FRONTEND_PORT%"
echo.
echo   前端地址：http://localhost:%FRONTEND_PORT%
echo   接口文档：http://127.0.0.1:%BACKEND_PORT%/docs
echo.
echo   注意：本脚本只启动后端与前端。
echo         若要使用「用例自愈」页，请另外启动本地 Runner：
echo             venv\Scripts\python.exe local_runner\appium_runner.py --port 8765
echo.
echo   关闭本窗口不影响服务；停止服务请到那两个服务窗口按 Ctrl+C。
echo.
pause
exit /b 0

:no_venv
echo.
echo [错误] 未找到虚拟环境 Python：
echo        %PY%
echo.
echo 请先创建虚拟环境并安装依赖：
echo        python -m venv venv
echo        venv\Scripts\python.exe -m pip install -r requirements.txt
echo.
pause
exit /b 1
