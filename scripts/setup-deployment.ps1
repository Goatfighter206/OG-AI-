# Deployment Configuration Script
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
Write-Host "📁 Creating deployment directory..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path $envDeployPath | Out-Null
Write-Host "✓ Directory created: $envDeployPath" -ForegroundColor Green

# Create environment configuration
Write-Host "⚙️  Creating environment configuration..." -ForegroundColor Green

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

# Create deployment script
$deployScript = @"
# Deployment Script for OG-AI - $Environment
param(
    [Parameter(Mandatory=`$false)]
    [string]`$PackagePath
)

`$ErrorActionPreference = "Stop"

Write-Host "================================" -ForegroundColor Cyan
Write-Host "Deploying OG-AI to $Environment" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Stop existing service if running
`$serviceName = "$serviceName"
if (Get-Service -Name `$serviceName -ErrorAction SilentlyContinue) {
    Write-Host "Stopping service: `$serviceName..." -ForegroundColor Yellow
    Stop-Service -Name `$serviceName -Force
    Write-Host "✓ Service stopped" -ForegroundColor Green
}

# Backup current deployment
if (Test-Path "$envDeployPath\ai_agent.py") {
    `$backupPath = "$envDeployPath\backup-`$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    Write-Host "Creating backup: `$backupPath..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Force -Path `$backupPath | Out-Null
    Copy-Item -Path "$envDeployPath\*" -Destination `$backupPath -Recurse -Exclude "backup-*","_work","logs" -ErrorAction SilentlyContinue
    Write-Host "✓ Backup created" -ForegroundColor Green
}

# Extract new package if provided
if (`$PackagePath -and (Test-Path `$PackagePath)) {
    Write-Host "Extracting package: `$PackagePath..." -ForegroundColor Yellow
    Expand-Archive -Path `$PackagePath -DestinationPath "$envDeployPath" -Force
    Write-Host "✓ Package extracted" -ForegroundColor Green
}

# Install dependencies
Write-Host "Installing dependencies..." -ForegroundColor Yellow
Set-Location "$envDeployPath"
pip install -r requirements.txt --quiet
Write-Host "✓ Dependencies installed" -ForegroundColor Green

# Start service if it exists
if (Get-Service -Name `$serviceName -ErrorAction SilentlyContinue) {
    Write-Host "Starting service: `$serviceName..." -ForegroundColor Yellow
    Start-Service -Name `$serviceName
    Write-Host "✓ Service started" -ForegroundColor Green
    
    # Wait and verify
    Start-Sleep -Seconds 5
    `$status = (Get-Service -Name `$serviceName).Status
    if (`$status -eq "Running") {
        Write-Host "✅ Deployment successful! Service is running." -ForegroundColor Green
    } else {
        Write-Error "❌ Service failed to start. Status: `$status"
        exit 1
    }
} else {
    Write-Host "⚠️  Service not configured. To run manually:" -ForegroundColor Yellow
    Write-Host "   cd $envDeployPath" -ForegroundColor Cyan
    Write-Host "   python app.py" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Deployment Complete!" -ForegroundColor Green
Write-Host "================================" -ForegroundColor Cyan
"@

$deployScript | Out-File -FilePath "$envDeployPath\deploy.ps1" -Encoding UTF8
Write-Host "✓ Deployment script created" -ForegroundColor Green

# Create service installation script
$serviceScript = @"
# Install OG-AI as Windows Service
# Run this script as Administrator

`$ErrorActionPreference = "Stop"

# Check if running as admin
`$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not `$isAdmin) {
    Write-Error "This script must be run as Administrator!"
    exit 1
}

Write-Host "Installing OG-AI as Windows Service..." -ForegroundColor Green

`$serviceName = "$serviceName"
`$serviceDisplayName = "OG-AI Service - $Environment"
`$serviceDescription = "OG-AI Conversational Agent - $Environment Environment"
`$servicePath = "$envDeployPath"
`$pythonExe = (Get-Command python).Source
`$appScript = "`$servicePath\app.py"

# Remove existing service if it exists
if (Get-Service -Name `$serviceName -ErrorAction SilentlyContinue) {
    Write-Host "Removing existing service..." -ForegroundColor Yellow
    Stop-Service -Name `$serviceName -Force -ErrorAction SilentlyContinue
    & sc.exe delete `$serviceName
    Start-Sleep -Seconds 2
}

# Create NSSM wrapper (if NSSM is available)
if (Get-Command nssm -ErrorAction SilentlyContinue) {
    Write-Host "Installing service with NSSM..." -ForegroundColor Yellow
    nssm install `$serviceName `$pythonExe `$appScript
    nssm set `$serviceName AppDirectory `$servicePath
    nssm set `$serviceName DisplayName "`$serviceDisplayName"
    nssm set `$serviceName Description "`$serviceDescription"
    nssm set `$serviceName Start SERVICE_AUTO_START
    nssm set `$serviceName AppStdout "`$servicePath\logs\service-out.log"
    nssm set `$serviceName AppStderr "`$servicePath\logs\service-err.log"
    nssm set `$serviceName AppRotateFiles 1
    nssm set `$serviceName AppRotateBytes 1048576
    
    Write-Host "✓ Service installed with NSSM" -ForegroundColor Green
    Write-Host ""
    Write-Host "To manage the service:" -ForegroundColor Yellow
    Write-Host "  Start:   nssm start `$serviceName" -ForegroundColor Cyan
    Write-Host "  Stop:    nssm stop `$serviceName" -ForegroundColor Cyan
    Write-Host "  Restart: nssm restart `$serviceName" -ForegroundColor Cyan
    Write-Host "  Remove:  nssm remove `$serviceName confirm" -ForegroundColor Cyan
} else {
    Write-Host "⚠️  NSSM not found. Install NSSM for service management:" -ForegroundColor Yellow
    Write-Host "   choco install nssm" -ForegroundColor Cyan
    Write-Host "   or download from: https://nssm.cc/download" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Alternatively, use Task Scheduler to run at startup:" -ForegroundColor Yellow
    Write-Host "   `$pythonExe `$appScript" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "✅ Service installation complete!" -ForegroundColor Green
"@

$serviceScript | Out-File -FilePath "$envDeployPath\install-service.ps1" -Encoding UTF8
Write-Host "✓ Service installation script created" -ForegroundColor Green

# Create logs directory
Write-Host "📁 Creating logs directory..." -ForegroundColor Green
New-Item -ItemType Directory -Force -Path "$envDeployPath\logs" | Out-Null
Write-Host "✓ Logs directory created" -ForegroundColor Green

# Create README for deployment
$readmeContent = @"
# OG-AI Deployment - $Environment Environment

**Deployment Path:** $envDeployPath
**Service Name:** $serviceName
**Port:** $Port

## Quick Start

### 1. Deploy Application
``````powershell
.\deploy.ps1 -PackagePath "path\to\og-ai-package.zip"
``````

### 2. Install as Service (Optional)
Run PowerShell as Administrator:
``````powershell
.\install-service.ps1
``````

### 3. Manual Start
``````powershell
python app.py
``````

## Service Management

### Using NSSM
``````powershell
nssm start $serviceName
nssm stop $serviceName
nssm restart $serviceName
nssm status $serviceName
``````

### Using Windows Services
``````powershell
Start-Service -Name $serviceName
Stop-Service -Name $serviceName
Restart-Service -Name $serviceName
Get-Service -Name $serviceName
``````

## Testing

### Health Check
``````powershell
Invoke-WebRequest -Uri "http://localhost:$Port/health"
``````

### Send Message
``````powershell
Invoke-RestMethod -Uri "http://localhost:$Port/chat" ``
    -Method Post ``
    -ContentType "application/json" ``
    -Body '{"message": "Hello!"}'
``````

## Logs

- Application logs: ``$envDeployPath\logs\``
- Service output: ``$envDeployPath\logs\service-out.log``
- Service errors: ``$envDeployPath\logs\service-err.log``

## Environment Variables

Edit ``.env`` file to configure:
- ``PORT``: Application port
- ``ENVIRONMENT``: Environment name
- ``LOG_LEVEL``: Logging level
- ``MAX_WORKERS``: Number of workers
- ``TIMEOUT``: Request timeout

## Backups

Backups are automatically created in: ``$envDeployPath\backup-*``

## Troubleshooting

### Check Service Status
``````powershell
Get-Service -Name $serviceName | Select-Object *
``````

### View Logs
``````powershell
Get-Content "$envDeployPath\logs\service-out.log" -Tail 50
Get-Content "$envDeployPath\logs\service-err.log" -Tail 50
``````

### Test Manually
``````powershell
cd $envDeployPath
python app.py
``````

---
*Generated: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss")*
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
Write-Host "  3. Install service (optional): $envDeployPath\install-service.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "Documentation: $envDeployPath\README.md" -ForegroundColor Yellow
Write-Host ""
