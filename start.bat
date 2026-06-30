@echo off
echo Checking dependencies...

:: 1. Verify Docker is installed
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: docker is not installed. Please install Docker Desktop and try again.
    exit /b 1
)

:: 2. Verify Docker is running
docker info >nul 2>nul
if %errorlevel% neq 0 (
    echo Error: Docker daemon is not running. Please start Docker Desktop and try again.
    exit /b 1
)

echo Docker daemon verified. Building and starting containers...
docker compose up --build -d

echo Waiting for services to become healthy...
set retry=0
:loop
set /a retry+=1
if %retry% gtr 30 (
    echo Error: Services did not become healthy in time.
    exit /b 1
)
timeout /t 2 >nul
powershell -Command "$res = Invoke-WebRequest -Uri 'http://localhost/api/v1/health' -UseBasicParsing -ErrorAction SilentlyContinue; if ($res.StatusCode -ne 200) { exit 1 }" >nul 2>nul
if %errorlevel% neq 0 (
    echo | set /p="."
    goto loop
)

echo.
echo =================================================
echo MCX Gold & Silver API
echo =================================================
echo.
echo Status:
echo READY
echo.
echo Dashboard:
echo http://localhost
echo.
echo Swagger:
echo http://localhost/docs
echo.
echo REST API:
echo http://localhost/api/v1/prices
echo.
echo WebSocket:
echo ws://localhost/api/v1/ws
echo.
echo Default API Key:
echo mcx_pub_dev_key
echo.
echo =================================================
