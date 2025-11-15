# Ultra Simple Deployment Setup
param(
    [string]$Environment = "staging",
    [string]$DeployPath = "C:\deployments\og-ai",
    [int]$Port = 8000
)

Write-Host "Setting up OG-AI deployment for $Environment..." -ForegroundColor Green

$envDeployPath = "$DeployPath\$Environment"

# Create directory
New-Item -ItemType Directory -Force -Path $envDeployPath | Out-Null
Write-Host "Created: $envDeployPath" -ForegroundColor Green

# Create .env file
$envContent = "PORT=$Port`nENVIRONMENT=$Environment`nAPP_NAME=og-ai"
$envContent | Out-File -FilePath "$envDeployPath\.env" -Encoding UTF8
Write-Host "Created .env file" -ForegroundColor Green

# Create deploy script
$sourcePath = "c:\Users\willi\ai-agents-deploy\OG-AI-"
$deployContent = "Write-Host 'Deploying...' -ForegroundColor Green`nCopy-Item -Path '$sourcePath\ai_agent.py','$sourcePath\app.py','$sourcePath\config.json','$sourcePath\requirements.txt' -Destination '$envDeployPath' -Force`nSet-Location '$envDeployPath'`npip install -r requirements.txt --quiet`nWrite-Host 'Done!' -ForegroundColor Green"
$deployContent | Out-File -FilePath "$envDeployPath\deploy.ps1" -Encoding UTF8
Write-Host "Created deploy.ps1" -ForegroundColor Green

# Create logs directory
New-Item -ItemType Directory -Force -Path "$envDeployPath\logs" | Out-Null
Write-Host "Created logs directory" -ForegroundColor Green

Write-Host "Setup complete! Check: $envDeployPath" -ForegroundColor Cyan