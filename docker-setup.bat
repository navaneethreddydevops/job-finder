@echo off
REM Job Finder Docker Setup Script (Windows)
REM ========================================
REM This script helps set up the Docker environment with proper Claude OAuth authentication

setlocal enabledelayedexpansion

echo.
echo 🚀 Job Finder Docker Setup (Windows)
echo =====================================
echo.

REM Check if Claude CLI is installed
where claude >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Claude CLI not found
    echo.
    echo Please install the Claude CLI first:
    echo   https://github.com/anthropics/claude-cli
    echo.
    echo Installation steps:
    echo   1. Download from: https://github.com/anthropics/claude-cli/releases
    echo   2. Extract and add to your PATH
    echo   3. Run: claude login
    exit /b 1
)
echo [OK] Claude CLI found

REM Check if Claude credentials exist
if not exist "%USERPROFILE%\.claude" (
    echo [WARNING] Claude credentials not found at %USERPROFILE%\.claude
    echo.
    echo Run the following to authenticate with Claude:
    echo   claude login
    echo.
    exit /b 1
)
echo [OK] Claude credentials found at %USERPROFILE%\.claude

REM Copy .env.example to .env if it doesn't exist
if not exist ".env" (
    echo.
    echo Creating .env from .env.example...
    copy .env.example .env
    echo [OK] Created .env
) else (
    echo [INFO] .env already exists, skipping copy
)

REM Set up Claude credentials for Docker
echo.
echo Setting up Claude credentials for Docker...

REM Check if local .claude exists in project
if not exist ".\.claude" (
    echo Copying Claude credentials to .\.claude...
    xcopy "%USERPROFILE%\.claude" ".\.claude" /E /I /Q
    echo [OK] Claude credentials copied to .\.claude
) else (
    echo [INFO] .\.claude already exists
    setlocal enabledelayedexpansion
    choice /C YN /M "Update from %USERPROFILE%\.claude? (Y/N)"
    if errorlevel 2 goto :skip_update
    if errorlevel 1 (
        xcopy "%USERPROFILE%\.claude" ".\.claude" /E /I /Q /Y
        echo [OK] Claude credentials updated
    )
    :skip_update
    endlocal
)

REM Check Docker
echo.
echo Checking Docker installation...
where docker >nul 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Docker not found
    echo Please install Docker: https://www.docker.com/products/docker-desktop
    exit /b 1
)
echo [OK] Docker found

REM Check Docker Compose
docker compose version >nul 2>nul
if %errorlevel% neq 0 (
    docker-compose --version >nul 2>nul
    if %errorlevel% neq 0 (
        echo [ERROR] Docker Compose not found
        echo Please install Docker Compose
        exit /b 1
    )
)
echo [OK] Docker Compose found

echo.
echo [SUCCESS] Setup complete!
echo.
echo Next steps:
echo 1. Review and customize .env file if needed
echo 2. Build and start the services:
echo.
echo    docker compose build
echo    docker compose up -d
echo.
echo 3. View logs:
echo    docker compose logs -f
echo.
echo 4. Access the application:
echo    Frontend:  http://localhost:5173
echo    Backend:   http://localhost:8000
echo.
echo To stop the services:
echo    docker compose down
echo.
echo For more information, see the README.md and CLAUDE.md files.
