@echo off
echo Starting DeepSeek Engineer v2 (Token-Aware Edition)...
echo.
echo This version includes token management to prevent API limit errors.
echo.

REM Check if python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo Please install Python 3.11 or later.
    pause
    exit /b 1
)

REM Check if .env file exists
if not exist ".env" (
    echo WARNING: .env file not found!
    echo.
    echo Please create a .env file with your DeepSeek API key:
    echo.
    echo For PowerShell:
    echo   Set-Content -Path .env -Value "DEEPSEEK_API_KEY=your_api_key_here" -Encoding UTF8
    echo.
    echo For Command Prompt:
    echo   echo DEEPSEEK_API_KEY=your_api_key_here > .env
    echo.
    pause
    exit /b 1
)

REM Launch the improved application
echo Launching DeepSeek Engineer v2 with token management...
python deepseek-eng-v2.py

REM Keep window open if there's an error
if errorlevel 1 (
    echo.
    echo An error occurred. Press any key to exit.
    pause
)
