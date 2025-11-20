# Test OG-AI Deployment Script
# Tests your deployed API at https://og-ai.onrender.com

Write-Host "Testing OG-AI Deployment..." -ForegroundColor Cyan
Write-Host ""

$baseUrl = "https://og-ai.onrender.com"

# Test 1: Health Check
Write-Host "[1] Testing Health Endpoint..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -Method Get -TimeoutSec 30
    Write-Host "[OK] Health Check: " -ForegroundColor Green -NoNewline
    Write-Host "$($health.status) - $($health.agent_name)"
    Write-Host ""
} catch {
    Write-Host "[FAIL] Health check failed: $_" -ForegroundColor Red
    exit 1
}

# Test 2: Chat Endpoint
Write-Host "[2] Testing Chat Endpoint..." -ForegroundColor Yellow
try {
    $chatBody = @{ message = "Hello! Tell me a fun fact." } | ConvertTo-Json
    $chatResponse = Invoke-RestMethod -Uri "$baseUrl/chat" -Method Post -Body $chatBody -ContentType "application/json" -TimeoutSec 30
    Write-Host "[OK] Chat Response:" -ForegroundColor Green
    Write-Host "   Agent: $($chatResponse.agent_name)"
    Write-Host "   Message: $($chatResponse.response)"
    Write-Host ""
} catch {
    Write-Host "[FAIL] Chat test failed: $_" -ForegroundColor Red
    exit 1
}

# Test 3: History Endpoint
Write-Host "[3] Testing History Endpoint..." -ForegroundColor Yellow
try {
    $history = Invoke-RestMethod -Uri "$baseUrl/history" -Method Get -TimeoutSec 30
    Write-Host "[OK] History Retrieved:" -ForegroundColor Green
    Write-Host "   Message Count: $($history.message_count)"
    Write-Host ""
} catch {
    Write-Host "[FAIL] History test failed: $_" -ForegroundColor Red
    exit 1
}

# Test 4: Interactive Documentation
Write-Host "[4] API Documentation Available at:" -ForegroundColor Yellow
Write-Host "   $baseUrl/docs" -ForegroundColor Cyan
Write-Host ""

# Summary
Write-Host "SUCCESS: All Tests Passed!" -ForegroundColor Green
Write-Host ""
Write-Host "Your API is live and working!" -ForegroundColor Green
Write-Host "Base URL: $baseUrl" -ForegroundColor Cyan
Write-Host ""
Write-Host "Quick Commands:" -ForegroundColor Yellow
Write-Host "  Health:  Invoke-RestMethod -Uri '$baseUrl/health'" -ForegroundColor Gray
Write-Host "  Chat:    `$body = @{ message = 'Hi!' } | ConvertTo-Json; Invoke-RestMethod -Uri '$baseUrl/chat' -Method Post -Body `$body -ContentType 'application/json'" -ForegroundColor Gray
Write-Host "  History: Invoke-RestMethod -Uri '$baseUrl/history'" -ForegroundColor Gray
Write-Host ""
Write-Host "For more info, see USAGE_GUIDE.md" -ForegroundColor Cyan

