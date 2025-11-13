# GitHub Actions Self-Hosted Runner Guide

This guide will help you set up and configure a GitHub Actions self-hosted runner for the OG-AI project.

## Table of Contents

- [Overview](#overview)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Manual Setup](#manual-setup)
- [Automated Setup](#automated-setup)
- [CI/CD Pipeline](#cicd-pipeline)
- [Deployment](#deployment)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

## Overview

A self-hosted runner gives you more control over the hardware, operating system, and software tools than GitHub-hosted runners provide. This is particularly useful for:

- **Cost savings** - No minute usage charges
- **Custom hardware** - Use your own powerful machines
- **Faster builds** - No queue time, direct access to your infrastructure
- **Network access** - Access to internal services and databases
- **Custom software** - Pre-installed tools and dependencies

## Prerequisites

### System Requirements

- **Operating System**: Windows 10/11, Windows Server 2019+, Linux, or macOS
- **Python**: 3.11 or higher
- **PowerShell**: 5.1 or higher (Windows) or PowerShell Core 7+ (cross-platform)
- **Memory**: 4GB RAM minimum, 8GB+ recommended
- **Disk Space**: 10GB minimum free space
- **Network**: Stable internet connection

### Required Software

- Git
- Python 3.11+
- pip (Python package manager)
- Docker (optional, for Docker builds)
- NSSM (optional, for Windows service installation)

### GitHub Permissions

- Repository admin access or "Manage runners" permission
- Ability to create GitHub Actions workflows

## Quick Start

### Step 1: Run the Automated Setup Script

```powershell
# Navigate to the OG-AI directory
cd c:\Users\willi\ai-agents-deploy\OG-AI-

# Run the setup script
.\scripts\setup-runner.ps1
```

### Step 2: Enter Your Runner Token

1. Go to: <https://github.com/Goatfighter206/OG-AI-/settings/actions/runners/new>
2. Copy the token from the configuration command
3. Paste it when prompted

### Step 3: Start the Runner

```powershell
# Start interactively (for testing)
cd $env:USERPROFILE\actions-runner
.\run.cmd

# Or install as a service (recommended for production)
.\svc.sh install
.\svc.sh start
```

That's it! Your runner is now active.

## Manual Setup

If you prefer manual setup or need more control:

### 1. Create Runner Directory

```powershell
mkdir $env:USERPROFILE\actions-runner
cd $env:USERPROFILE\actions-runner
```

### 2. Download Runner Package

```powershell
# Download latest version (check GitHub for current version)
$version = "2.321.0"
$url = "https://github.com/actions/runner/releases/download/v$version/actions-runner-win-x64-$version.zip"
Invoke-WebRequest -Uri $url -OutFile "actions-runner-win-x64-$version.zip"
```

### 3. Extract Package

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory("$PWD\actions-runner-win-x64-$version.zip", "$PWD")
```

### 4. Configure Runner

```powershell
# Get your token from: https://github.com/Goatfighter206/OG-AI-/settings/actions/runners/new
$token = "YOUR_TOKEN_HERE"

.\config.cmd --url https://github.com/Goatfighter206/OG-AI- --token $token
```

You'll be prompted for:

- **Runner name**: e.g., `my-windows-runner`
- **Runner group**: Press Enter for default
- **Work folder**: Press Enter for default (`_work`)
- **Labels**: Add custom labels or press Enter for default

### 5. Start Runner

**Interactive (for testing):**

```powershell
.\run.cmd
```

**As a service (recommended):**

```powershell
# Install service (requires admin)
.\svc.sh install

# Start service
.\svc.sh start

# Check status
.\svc.sh status
```

## Automated Setup

Use the provided setup script for a streamlined installation:

### Basic Usage

```powershell
.\scripts\setup-runner.ps1
```

### Advanced Usage

```powershell
# Custom runner name
.\scripts\setup-runner.ps1 -RunnerName "production-runner"

# Custom installation path
.\scripts\setup-runner.ps1 -InstallPath "C:\runners\og-ai"

# Install as service (requires admin)
.\scripts\setup-runner.ps1 -InstallAsService

# All options combined
.\scripts\setup-runner.ps1 `
    -RunnerName "og-ai-production" `
    -InstallPath "C:\runners\og-ai" `
    -InstallAsService `
    -RunnerToken "YOUR_TOKEN"
```

### Script Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `RunnerName` | Name for the runner | `windows-runner-XXXX` |
| `RunnerToken` | GitHub runner token | Prompts if not provided |
| `InstallPath` | Installation directory | `$env:USERPROFILE\actions-runner` |
| `InstallAsService` | Install as Windows service | `false` |
| `RunnerVersion` | Runner version to install | `2.321.0` |

## CI/CD Pipeline

The project includes a comprehensive CI/CD workflow at `.github/workflows/self-hosted-ci.yml`.

### Workflow Features

#### 1. **Test Job** 🧪

- Runs unit tests with pytest
- Generates coverage reports
- Validates AI agent functionality
- Uploads test artifacts

#### 2. **Security Job** 🔒

- Bandit security scanning
- Dependency vulnerability checking with pip-audit
- Safety checks for known vulnerabilities
- Generates security reports

#### 3. **Lint Job** 📝

- Code formatting with Black
- Import sorting with isort
- Flake8 code quality checks
- Pylint static analysis
- Type checking with MyPy

#### 4. **Performance Job** ⚡

- Load testing (100 requests)
- Response time measurement
- API health verification

#### 5. **Build & Deploy Job** 🚀

- Creates deployment packages
- Deploys to staging/production
- Manages Windows services
- Archives deployment artifacts

#### 6. **Docker Build Job** 🐳

- Builds Docker images
- Tests container functionality
- Saves images for deployment
- Multi-tag support

#### 7. **Notify Job** 📢

- Comprehensive status summary
- Deployment information
- Failed job identification

### Trigger Conditions

The workflow runs on:

- Push to `main` or `develop` branches
- Pull requests to `main`
- Manual trigger via workflow_dispatch

### Manual Workflow Trigger

```powershell
# Trigger via GitHub CLI
gh workflow run self-hosted-ci.yml

# With environment selection
gh workflow run self-hosted-ci.yml -f deploy_environment=production
```

Or use the GitHub web interface:

1. Go to Actions tab
2. Select "OG-AI Self-Hosted CI/CD"
3. Click "Run workflow"
4. Select environment (staging/production)

## Deployment

### Automated Deployment

The CI/CD pipeline automatically deploys to `C:\deployments\og-ai\{environment}` on the `main` branch.

### Manual Deployment

#### Step 1: Setup Deployment Environment

```powershell
# Setup staging environment
.\scripts\setup-deployment.ps1 -Environment staging

# Setup production environment
.\scripts\setup-deployment.ps1 -Environment production -Port 8080
```

#### Step 2: Deploy Application

```powershell
# Navigate to deployment directory
cd C:\deployments\og-ai\staging

# Deploy a package
.\deploy.ps1 -PackagePath "path\to\og-ai-package.zip"
```

#### Step 3: Install as Windows Service (Optional)

```powershell
# Run as Administrator
.\install-service.ps1
```

### Service Management

```powershell
# Start service
Start-Service -Name OG-AI-staging

# Stop service
Stop-Service -Name OG-AI-staging

# Restart service
Restart-Service -Name OG-AI-staging

# Check status
Get-Service -Name OG-AI-staging
```

### Using NSSM (Recommended)

```powershell
# Install NSSM
choco install nssm

# Manage service
nssm start OG-AI-staging
nssm stop OG-AI-staging
nssm restart OG-AI-staging
nssm status OG-AI-staging
```

## Security

### Security Scanning Tools

The project includes several security tools configured in the CI/CD pipeline:

#### 1. Bandit

Configuration: `.bandit`

- Scans Python code for security issues
- Identifies common vulnerabilities
- Generates JSON and text reports

#### 2. pip-audit

- Checks dependencies for known vulnerabilities
- Uses PyPI Advisory Database
- Reports CVEs and security advisories

#### 3. Safety

Configuration: `.safety-policy.yml`

- Checks dependencies against safety database
- Configurable severity levels
- JSON report generation

### Best Practices

1. **Never commit secrets** to the repository
2. **Use environment variables** for sensitive data
3. **Keep dependencies updated** regularly
4. **Review security reports** from CI/CD pipeline
5. **Enable branch protection** on main branch
6. **Require reviews** for pull requests
7. **Use HTTPS** for production deployments
8. **Enable authentication** for API endpoints

### Code Quality Tools

Configuration files:

- `.flake8` - PEP 8 compliance
- `mypy.ini` - Static type checking
- `pyproject.toml` - Black, isort, pytest, coverage

## Troubleshooting

### Runner Issues

#### Runner Not Showing Up

```powershell
# Check runner status
cd $env:USERPROFILE\actions-runner
.\run.cmd --check

# View configuration
cat .runner

# Check logs
Get-Content _diag/*.log -Tail 50
```

#### Runner Offline

```powershell
# Restart the service
Restart-Service -Name "actions.runner.Goatfighter206-OG-AI-.YOUR_RUNNER_NAME"

# Or restart manually
cd $env:USERPROFILE\actions-runner
.\svc.sh stop
.\svc.sh start
```

#### Token Expired

```powershell
# Get new token from GitHub
# Go to: https://github.com/Goatfighter206/OG-AI-/settings/actions/runners

# Reconfigure runner
.\config.cmd remove --token OLD_TOKEN
.\config.cmd --url https://github.com/Goatfighter206/OG-AI- --token NEW_TOKEN
```

### Workflow Issues

#### Job Stuck in Queue

- Verify runner is online and idle
- Check runner labels match job requirements
- Ensure runner has sufficient resources

#### Job Failing

```powershell
# Check workflow logs in GitHub Actions tab
# Run job locally for debugging

# Test Python environment
python --version
pip list

# Test dependencies
pip install -r requirements.txt

# Run tests manually
pytest -v
```

#### Permission Errors

```powershell
# Ensure runner has write access to work directory
# Check Windows service permissions
# Run PowerShell as Administrator if needed
```

### Deployment Issues

#### Service Won't Start

```powershell
# Check service status
Get-Service -Name OG-AI-staging | Select-Object *

# View service logs
Get-Content C:\deployments\og-ai\staging\logs\service-err.log -Tail 50

# Test manually
cd C:\deployments\og-ai\staging
python app.py
```

#### Port Already in Use

```powershell
# Find process using the port
netstat -ano | findstr :8000

# Kill the process (replace PID)
Stop-Process -Id PID -Force

# Or change port in .env file
```

#### Module Import Errors

```powershell
# Reinstall dependencies
pip install -r requirements.txt --force-reinstall

# Check Python path
python -c "import sys; print(sys.path)"

# Verify installation
python -c "from ai_agent import AIAgent; from app import app"
```

### Network Issues

#### Can't Reach API

```powershell
# Check if service is running
Get-Service -Name OG-AI-staging

# Test locally
Invoke-WebRequest -Uri "http://localhost:8000/health"

# Check firewall
netsh advfirewall firewall show rule name=all | findstr 8000

# Test from another machine
Test-NetConnection -ComputerName YOUR_IP -Port 8000
```

## Additional Resources

### GitHub Documentation

- [Self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners)
- [Adding self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/adding-self-hosted-runners)
- [Using self-hosted runners](https://docs.github.com/en/actions/hosting-your-own-runners/using-self-hosted-runners-in-a-workflow)

### Project Links

- Repository: <https://github.com/Goatfighter206/OG-AI->
- Actions: <https://github.com/Goatfighter206/OG-AI-/actions>
- Runners: <https://github.com/Goatfighter206/OG-AI-/settings/actions/runners>

### Support

For issues and questions:

1. Check this documentation
2. Review GitHub Actions logs
3. Check runner diagnostic logs
4. Create an issue on GitHub

---

*Last Updated: 2025-11-12*
*Version: 1.0.0*
