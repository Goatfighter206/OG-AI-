# GitHub Actions Self-Hosted Runner Setup Script
# This script automates the installation and configuration of a GitHub Actions runner

param(
    [Parameter(Mandatory = $false)]
    [string]$RunnerName = "windows-runner-$(Get-Random -Maximum 9999)",
    
    [Parameter(Mandatory = $false)]
    [string]$RunnerToken = "",
    
    [Parameter(Mandatory = $false)]
    [string]$InstallPath = "$env:USERPROFILE\actions-runner",
    
    [Parameter(Mandatory = $false)]
    [switch]$InstallAsService,
    
    [Parameter(Mandatory = $false)]
    [string]$RunnerVersion = "2.321.0"
)

Write-Host "================================" -ForegroundColor Cyan
Write-Host "GitHub Actions Runner Setup" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($InstallAsService -and -not $isAdmin) {
    Write-Error "Installing as a service requires administrator privileges. Please run PowerShell as Administrator."
    exit 1
}

# Repository information
$repoUrl = "https://github.com/Goatfighter206/OG-AI-"
$downloadUrl = "https://github.com/actions/runner/releases/download/v$RunnerVersion/actions-runner-win-x64-$RunnerVersion.zip"
$zipFile = "actions-runner-win-x64-$RunnerVersion.zip"

Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Runner Name: $RunnerName"
Write-Host "  Install Path: $InstallPath"
Write-Host "  Repository: $repoUrl"
Write-Host "  Install as Service: $InstallAsService"
Write-Host ""

# Prompt for token if not provided
if ([string]::IsNullOrWhiteSpace($RunnerToken)) {
    Write-Host "🔑 To get your runner token:" -ForegroundColor Yellow
    Write-Host "   1. Go to: https://github.com/Goatfighter206/OG-AI-/settings/actions/runners/new" -ForegroundColor Yellow
    Write-Host "   2. Copy the token from the configuration command" -ForegroundColor Yellow
    Write-Host ""
    $RunnerToken = Read-Host "Enter your GitHub Runner Token"
    
    if ([string]::IsNullOrWhiteSpace($RunnerToken)) {
        Write-Error "Runner token is required!"
        exit 1
    }
}

# Create installation directory
Write-Host "📁 Creating installation directory..." -ForegroundColor Green
if (Test-Path $InstallPath) {
    Write-Warning "Directory already exists: $InstallPath"
    $overwrite = Read-Host "Do you want to remove it and continue? (y/n)"
    if ($overwrite -eq 'y') {
        Remove-Item -Recurse -Force $InstallPath
    }
    else {
        Write-Host "Installation cancelled."
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
Set-Location $InstallPath

# Download runner package
Write-Host "⬇️  Downloading GitHub Actions Runner v$RunnerVersion..." -ForegroundColor Green
try {
    Invoke-WebRequest -Uri $downloadUrl -OutFile $zipFile -UseBasicParsing
    Write-Host "✓ Download complete" -ForegroundColor Green
}
catch {
    Write-Error "Failed to download runner package: $_"
    exit 1
}

# Verify checksum (optional but recommended)
Write-Host "🔐 Verifying download integrity..." -ForegroundColor Green
$hash = (Get-FileHash -Path $zipFile -Algorithm SHA256).Hash
Write-Host "   SHA256: $hash" -ForegroundColor Gray

# Extract runner package
Write-Host "📦 Extracting runner package..." -ForegroundColor Green
try {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory("$PWD\$zipFile", "$PWD")
    Remove-Item $zipFile
    Write-Host "✓ Extraction complete" -ForegroundColor Green
}
catch {
    Write-Error "Failed to extract runner package: $_"
    exit 1
}

# Configure the runner
Write-Host "⚙️  Configuring runner..." -ForegroundColor Green
try {
    $configArgs = @(
        "--url", $repoUrl,
        "--token", $RunnerToken,
        "--name", $RunnerName,
        "--work", "_work",
        "--unattended"
    )
    
    if (-not $InstallAsService) {
        $configArgs += "--replace"
    }
    
    & "$InstallPath\config.cmd" @configArgs
    
    if ($LASTEXITCODE -ne 0) {
        throw "Configuration failed with exit code $LASTEXITCODE"
    }
    
    Write-Host "✓ Runner configured successfully" -ForegroundColor Green
}
catch {
    Write-Error "Failed to configure runner: $_"
    exit 1
}

# Install and start as service if requested
if ($InstallAsService) {
    Write-Host "🔧 Installing runner as Windows service..." -ForegroundColor Green
    try {
        & "$InstallPath\svc.sh" install
        Write-Host "✓ Service installed" -ForegroundColor Green
        
        Write-Host "▶️  Starting runner service..." -ForegroundColor Green
        & "$InstallPath\svc.sh" start
        Write-Host "✓ Service started" -ForegroundColor Green
    }
    catch {
        Write-Error "Failed to install/start service: $_"
        exit 1
    }
}
else {
    Write-Host ""
    Write-Host "✅ Runner setup complete!" -ForegroundColor Green
    Write-Host ""
    Write-Host "To start the runner, run:" -ForegroundColor Yellow
    Write-Host "   cd $InstallPath" -ForegroundColor Cyan
    Write-Host "   .\run.cmd" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "To install as a service later (requires admin):" -ForegroundColor Yellow
    Write-Host "   .\svc.sh install" -ForegroundColor Cyan
    Write-Host "   .\svc.sh start" -ForegroundColor Cyan
}

Write-Host ""
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Runner Information" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host "Name: $RunnerName" -ForegroundColor White
Write-Host "Path: $InstallPath" -ForegroundColor White
Write-Host "Repository: $repoUrl" -ForegroundColor White
Write-Host ""
Write-Host "View your runner at:" -ForegroundColor Yellow
Write-Host "https://github.com/Goatfighter206/OG-AI-/settings/actions/runners" -ForegroundColor Cyan
Write-Host ""

# Create a quick start script
$startScript = @"
# Quick Start Script for GitHub Actions Runner
Write-Host "Starting GitHub Actions Runner..." -ForegroundColor Green
Set-Location "$InstallPath"
.\run.cmd
"@

$startScript | Out-File -FilePath "$InstallPath\start-runner.ps1" -Encoding UTF8
Write-Host "📝 Created quick start script: $InstallPath\start-runner.ps1" -ForegroundColor Green
Write-Host ""
