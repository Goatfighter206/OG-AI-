# Quick Start Guide - GitHub Actions Self-Hosted Runner

## 🚀 Complete Setup in 3 Steps

### Step 1: Run the Setup Script

```powershell
cd c:\Users\willi\ai-agents-deploy\OG-AI-
.\scripts\setup-runner.ps1
```

### Step 2: Get Your Token

1. Visit: <https://github.com/Goatfighter206/OG-AI-/settings/actions/runners/new>
2. Copy the token shown in the configuration command
3. Paste it when the script prompts you

### Step 3: Start the Runner

```powershell
# The script will create start-runner.ps1 for you
cd $env:USERPROFILE\actions-runner
.\start-runner.ps1
```

**That's it! Your runner is now active.** 🎉

---

## 📚 What Was Created

### 1. **CI/CD Workflow** `.github/workflows/self-hosted-ci.yml`

Complete automated pipeline with:

- ✅ Unit tests with coverage reports
- 🔒 Security scanning (Bandit, pip-audit, Safety)
- 📝 Code quality checks (Black, isort, flake8, pylint, mypy)
- ⚡ Performance testing
- 🚀 Automated deployment
- 🐳 Docker image building
- 📢 Status notifications

### 2. **Setup Scripts** `scripts/`

- **setup-runner.ps1**: Automated runner installation
- **setup-deployment.ps1**: Deployment environment configuration

### 3. **Security Configurations**

- `.bandit`: Bandit security linter config
- `.safety-policy.yml`: Safety check policy
- `.flake8`: Code quality rules
- `mypy.ini`: Type checking configuration
- `pyproject.toml`: Black, isort, pytest, coverage config

### 4. **Documentation**

- **RUNNER_SETUP.md**: Complete runner setup guide
- **SECURITY_GUIDE.md**: Security best practices and tools

---

## 🎯 Next Actions

### 1. Set Up the Runner

```powershell
# Run the automated setup
.\scripts\setup-runner.ps1 -InstallAsService

# Or follow manual instructions in RUNNER_SETUP.md
```

### 2. Push Changes to GitHub

```powershell
# Add all new files
git add .github/workflows/self-hosted-ci.yml
git add scripts/
git add .bandit .safety-policy.yml .flake8 mypy.ini
git add RUNNER_SETUP.md SECURITY_GUIDE.md QUICKSTART.md
git add pyproject.toml

# Commit
git commit -m "Add comprehensive CI/CD with self-hosted runner support

- Add automated GitHub Actions workflow with testing, security, and deployment
- Add runner setup scripts for easy installation
- Add security scanning tools (Bandit, pip-audit, Safety)
- Add code quality tools configuration
- Add comprehensive documentation"

# Push to GitHub
git push origin main
```

### 3. Verify Runner

1. Go to: <https://github.com/Goatfighter206/OG-AI-/settings/actions/runners>
2. Confirm your runner shows as "Idle" (green dot)
3. Check Actions tab: <https://github.com/Goatfighter206/OG-AI-/actions>
4. Watch your first workflow run!

### 4. Setup Deployment (Optional)

```powershell
# Configure staging environment
.\scripts\setup-deployment.ps1 -Environment staging

# Configure production environment
.\scripts\setup-deployment.ps1 -Environment production
```

---

## 📋 Workflow Triggers

The CI/CD pipeline automatically runs on:

- **Push** to `main` or `develop` branches
- **Pull requests** to `main` branch
- **Manual trigger** via GitHub UI

### Manual Trigger

1. Go to: <https://github.com/Goatfighter206/OG-AI-/actions>
2. Click "OG-AI Self-Hosted CI/CD"
3. Click "Run workflow"
4. Select environment (staging/production)
5. Click "Run workflow" button

---

## 🔧 Customization Options

### Workflow Customization

Edit `.github/workflows/self-hosted-ci.yml` to:

- Add/remove jobs
- Change deployment paths
- Modify test commands
- Add notification integrations (Slack, Discord, email)
- Configure Docker registry push

### Deployment Customization

Edit `scripts/setup-deployment.ps1` to:

- Change deployment paths
- Modify port numbers
- Add custom environment variables
- Configure service settings

### Security Customization

Edit security tool configs to:

- Adjust severity levels
- Add ignored vulnerabilities (with justification)
- Modify code quality rules
- Change test coverage requirements

---

## 📊 Viewing Reports

After each workflow run:

1. Go to: <https://github.com/Goatfighter206/OG-AI-/actions>
2. Click on a workflow run
3. View job summaries and logs
4. Download artifacts:
   - Test results (HTML reports)
   - Coverage reports
   - Security scan reports
   - Deployment packages
   - Docker images

---

## 🛠️ Troubleshooting

### Runner Not Showing Up

```powershell
# Check runner logs
cd $env:USERPROFILE\actions-runner
Get-Content _diag/*.log -Tail 50
```

### Workflow Failing

- Check job logs in GitHub Actions tab
- Verify runner is online and idle
- Ensure all dependencies are installed
- Check security scan warnings

### Deployment Issues

```powershell
# Check service status
Get-Service -Name OG-AI-staging

# View logs
Get-Content C:\deployments\og-ai\staging\logs\*.log -Tail 50

# Test manually
cd C:\deployments\og-ai\staging
python app.py
```

---

## 📖 Full Documentation

- **Runner Setup**: See `RUNNER_SETUP.md`
- **Security Guide**: See `SECURITY_GUIDE.md`
- **Project README**: See `README.md`

---

## ✅ Checklist

Before deploying to production:

- [ ] Runner is installed and running
- [ ] Workflow runs successfully
- [ ] All tests pass
- [ ] Security scans complete with no critical issues
- [ ] Deployment environment is configured
- [ ] Service is installed (if using Windows service)
- [ ] Firewall rules are configured
- [ ] HTTPS is configured (production)
- [ ] Monitoring is set up
- [ ] Backups are configured

---

## 🎉 You're All Set

Your OG-AI project now has:

- ✅ Fully automated CI/CD pipeline
- ✅ Self-hosted runner capability
- ✅ Comprehensive security scanning
- ✅ Automated testing and deployment
- ✅ Code quality enforcement
- ✅ Docker support
- ✅ Complete documentation

**Happy coding!** 🚀

---

*For questions or issues, see the documentation or create an issue on GitHub.*
