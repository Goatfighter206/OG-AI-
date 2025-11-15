# Simple Deployment Setup Script
# Prepares the environment for deployment

param(
    [Parameter(Mandatory = $false)]
    [ValidateSet("staging", "production")]
    [string]$Environment = "staging",

    [Parameter(Mandatory = $false)]
    [string]$DeployPath = "C:\deployments\og-ai",

    [Parameter(Mandatory = $false)]
    [int]$Port = 8000
)

Write-Host "================================" -ForegroundColor Cyan
Write-Host "OG-AI Deployment Configuration" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

$envDeployPath = "$DeployPath\$Environment"
$serviceName = "OG-AI-$Environment"

Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Environment: $Environment"
Write-Host "  Deploy Path: $envDeployPath"
Write-Host "  Service Name: $serviceName"
Write-Host "  Port: $Port"
Write-Host ""

# Create deployment directory
Write-Host "Creating deployment directory..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $envDeployPath | Out-Null
Write-Host "✓ Directory created: $envDeployPath" -ForegroundColor Green

# Create environment configuration
Write-Host "Creating environment configuration..." -ForegroundColor Green

$envConfig = @"
# Environment: $Environment
# Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")

PORT=$Port
ENVIRONMENT=$Environment
APP_NAME=og-ai
LOG_LEVEL=INFO
MAX_WORKERS=4
TIMEOUT=120
"@

$envConfig | Out-File -FilePath "$envDeployPath\.env" -Encoding UTF8
Write-Host "✓ Environment file created" -ForegroundColor Green

# Create simple deployment script
$deployScript = @"
Write-Host "Deploying OG-AI to $Environment..." -ForegroundColor Green
Write-Host "Copying files..." -ForegroundColor Yellow
Copy-Item -Path "ai_agent.py","app.py","config.json","requirements.txt" -Destination "$envDeployPath" -Force
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location "$envDeployPath"
pip install -r requirements.txt --quiet
Write-Host "✓ Deployment complete!" -ForegroundColor Green
"@

$deployScript | Out-File -FilePath "$envDeployPath\deploy.ps1" -Encoding UTF8
Write-Host "✓ Deployment script created" -ForegroundColor Green

# Create logs directory
Write-Host "Creating logs directory..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path "$envDeployPath\logs" | Out-Null
Write-Host "✓ Logs directory created" -ForegroundColor Green

# Create simple README
$readmeContent = @"
OG-AI Deployment - $Environment Environment

Deployment Path: $envDeployPath
Service Name: $serviceName
Port: $Port

Quick Start:
1. Deploy: .\deploy.ps1
2. Manual start: python app.py

Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
"@

$readmeContent | Out-File -FilePath "$envDeployPath\README.md" -Encoding UTF8
Write-Host "✓ README created" -ForegroundColor Green

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "✅ Deployment Configuration Complete!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Next Steps:" -ForegroundColor Yellow
Write-Host "  1. Review configuration: $envDeployPath\.env" -ForegroundColor Cyan
Write-Host "  2. Deploy application: $envDeployPath\deploy.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "Documentation: $envDeployPath\README.md" -ForegroundColor Yellow
Write-Host ""