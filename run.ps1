#!/usr/bin/env pwsh
# DeepSeek Engineer Launcher for PowerShell

Write-Host "Starting DeepSeek Engineer..." -ForegroundColor Cyan
Write-Host ""

# Check if Python is available
try {
    $pythonVersion = python --version 2>&1
    Write-Host "Found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "ERROR: Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.11 or later." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

# Check if .env file exists
if (-not (Test-Path ".env")) {
    Write-Host "WARNING: .env file not found!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Please create a .env file with your DeepSeek API key:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Run this command:" -ForegroundColor Cyan
    Write-Host '  Set-Content -Path .env -Value "DEEPSEEK_API_KEY=your_api_key_here" -Encoding UTF8' -ForegroundColor White
    Write-Host ""
    Read-Host "Press Enter to exit"
    exit 1
}

# Set UTF-8 encoding for the console
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"

# Launch the application
Write-Host "Launching DeepSeek Engineer..." -ForegroundColor Green
Write-Host ""

python deepseek-eng.py

# Check if there was an error
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "An error occurred (Exit code: $LASTEXITCODE)" -ForegroundColor Red
    Read-Host "Press Enter to exit"
}
