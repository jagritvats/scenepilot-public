# ScenePilot - Local Development Starter
Write-Host "Starting ScenePilot (FastAPI Backend + Next.js Frontend)..." -ForegroundColor Cyan

$root = $PSScriptRoot

# 1. Start Agent Backend (Port 8000)
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\services\agent'; Write-Host 'Starting ScenePilot Agent Service on http://localhost:8000...' -ForegroundColor Green; uv run uvicorn scenepilot.api.app:app --reload --port 8000"

# 2. Start Web Frontend (Port 3000)
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd '$root\apps\web'; Write-Host 'Starting Next.js Control Room on http://localhost:3000...' -ForegroundColor Green; pnpm dev"

Write-Host "Services launched!" -ForegroundColor Green
Write-Host "  -> Backend API: http://localhost:8000 (docs at http://localhost:8000/docs)" -ForegroundColor Yellow
Write-Host "  -> Frontend App: http://localhost:3000" -ForegroundColor Yellow
Start-Process "http://localhost:3000"
