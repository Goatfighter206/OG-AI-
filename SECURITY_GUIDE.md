# Security Scanning Guide

This document describes the security tools and practices implemented in the OG-AI project.

## Overview

The project uses multiple security scanning tools integrated into the CI/CD pipeline to identify potential vulnerabilities, insecure code patterns, and dependency issues.

## Security Tools

### 1. Bandit

**Purpose**: Static analysis for Python code security issues

**Configuration**: `.bandit`

**What it checks**:

- Hardcoded passwords and secrets
- SQL injection vulnerabilities
- Shell injection risks
- Use of insecure functions (eval, exec, pickle)
- Weak cryptography
- Insecure random number generation
- XML vulnerabilities
- Flask debug mode in production

**Usage**:

```powershell
# Install
pip install bandit

# Run scan
bandit -r . -f txt

# Generate JSON report
bandit -r . -f json -o bandit-report.json

# Scan specific file
bandit ai_agent.py app.py
```

### 2. pip-audit

**Purpose**: Check Python dependencies for known security vulnerabilities

**What it checks**:

- CVEs in installed packages
- Security advisories from PyPI
- Vulnerable package versions

**Usage**:

```powershell
# Install
pip install pip-audit

# Run audit
pip-audit

# With detailed descriptions
pip-audit --desc

# Generate JSON report
pip-audit --format json > pip-audit-report.json

# Fix vulnerabilities
pip-audit --fix
```

### 3. Safety

**Purpose**: Check dependencies against safety database

**Configuration**: `.safety-policy.yml`

**What it checks**:

- Known security vulnerabilities
- Outdated packages with security issues
- CVEs and security advisories

**Usage**:

```powershell
# Install
pip install safety

# Run check
safety check

# Generate JSON report
safety check --json > safety-report.json

# Check specific requirements file
safety check -r requirements.txt

# Set severity threshold
safety check --severity high
```

## CI/CD Integration

The security job in `.github/workflows/self-hosted-ci.yml` automatically runs all security tools on every push and pull request.

### Security Job Steps

1. **Bandit Scan**
   - Scans all Python files
   - Generates reports
   - Continues on error (warnings only)

2. **pip-audit Check**
   - Audits installed dependencies
   - Shows detailed vulnerability descriptions
   - Continues on error (warnings only)

3. **Safety Check**
   - Checks against safety database
   - Generates JSON report
   - Continues on error (warnings only)

4. **Upload Reports**
   - Uploads all security reports as artifacts
   - Available for 30 days
   - Downloadable from GitHub Actions

## Security Best Practices

### Code Security

1. **Never hardcode secrets**

   ```python
   # ❌ Bad
   API_KEY = "sk-1234567890abcdef"
   
   # ✅ Good
   import os
   API_KEY = os.getenv("API_KEY")
   ```

2. **Validate user input**

   ```python
   # ✅ Good
   from flask import request, jsonify
   
   @app.route('/chat', methods=['POST'])
   def chat():
       data = request.get_json()
       if not data or 'message' not in data:
           return jsonify({'error': 'Invalid request'}), 400
       
       message = str(data['message'])[:1000]  # Limit length
       # Process message...
   ```

3. **Use parameterized queries** (when using databases)

   ```python
   # ❌ Bad (SQL injection risk)
   cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
   
   # ✅ Good
   cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
   ```

4. **Disable debug mode in production**

   ```python
   # ❌ Bad
   app.run(debug=True)
   
   # ✅ Good
   app.run(debug=os.getenv('ENVIRONMENT') == 'development')
   ```

### Dependency Security

1. **Keep dependencies updated**

   ```powershell
   # Check for updates
   pip list --outdated
   
   # Update specific package
   pip install --upgrade package-name
   
   # Update all packages (use with caution)
   pip install --upgrade -r requirements.txt
   ```

2. **Pin dependency versions**

   ```text
   # requirements.txt
   Flask==2.3.0
   Flask-CORS==4.0.0
   gunicorn==21.2.0
   ```

3. **Use virtual environments**

   ```powershell
   # Create virtual environment
   python -m venv venv
   
   # Activate
   .\venv\Scripts\Activate.ps1
   
   # Install dependencies
   pip install -r requirements.txt
   ```

4. **Audit before deploying**

   ```powershell
   pip-audit
   safety check
   ```

### API Security

1. **Enable CORS properly**

   ```python
   from flask_cors import CORS
   
   # ✅ Restrict origins in production
   CORS(app, resources={
       r"/*": {
           "origins": ["https://yourdomain.com"],
           "methods": ["GET", "POST"],
           "allow_headers": ["Content-Type"]
       }
   })
   ```

2. **Implement rate limiting**

   ```python
   from flask_limiter import Limiter
   
   limiter = Limiter(
       app=app,
       key_func=lambda: request.remote_addr,
       default_limits=["100 per hour"]
   )
   ```

3. **Use HTTPS in production**
   - Configure SSL/TLS certificates
   - Use reverse proxy (nginx, Apache)
   - Enable HSTS headers

4. **Implement authentication**

   ```python
   from flask import request
   
   def require_api_key(f):
       def decorated_function(*args, **kwargs):
           api_key = request.headers.get('X-API-Key')
           if api_key != os.getenv('API_KEY'):
               return jsonify({'error': 'Unauthorized'}), 401
           return f(*args, **kwargs)
       return decorated_function
   
   @app.route('/protected')
   @require_api_key
   def protected():
       return jsonify({'data': 'sensitive'})
   ```

### Environment Security

1. **Use environment variables**

   ```powershell
   # .env file (never commit this!)
   API_KEY=your-secret-key
   DATABASE_URL=postgresql://user:pass@localhost/db
   SECRET_KEY=random-secret-string
   ```

2. **Secure file permissions**

   ```powershell
   # Restrict access to .env file
   icacls .env /inheritance:r /grant:r "$($env:USERNAME):(R)"
   ```

3. **Secure runner environment**
   - Run runner as non-admin user
   - Isolate runner in separate directory
   - Limit network access if possible
   - Regularly update runner software

## Vulnerability Response

### When a vulnerability is found

1. **Assess severity**
   - Critical: Immediate action required
   - High: Fix within 24 hours
   - Medium: Fix within 1 week
   - Low: Schedule for next release

2. **Update dependencies**

   ```powershell
   # Update specific package
   pip install --upgrade vulnerable-package
   
   # Verify fix
   pip-audit
   safety check
   ```

3. **Test changes**

   ```powershell
   # Run tests
   pytest -v
   
   # Test API
   python app.py
   ```

4. **Deploy fix**

   ```powershell
   # Commit changes
   git add requirements.txt
   git commit -m "security: update vulnerable-package to fix CVE-XXXX-XXXXX"
   git push
   ```

5. **Document the fix**
   - Update changelog
   - Create security advisory if public repo
   - Notify users if necessary

## Security Checklist

Before deployment, ensure:

- [ ] All dependencies are up to date
- [ ] No security vulnerabilities in pip-audit
- [ ] Bandit reports no high-severity issues
- [ ] Safety check passes
- [ ] Debug mode is disabled
- [ ] Secrets are in environment variables
- [ ] CORS is properly configured
- [ ] API authentication is enabled (if needed)
- [ ] HTTPS is configured (production)
- [ ] Rate limiting is enabled (if needed)
- [ ] Logs don't contain sensitive data
- [ ] File permissions are restricted
- [ ] Firewall rules are configured

## Monitoring and Alerts

### Continuous Monitoring

1. **Enable Dependabot** (GitHub)
   - Automatically checks for vulnerabilities
   - Creates pull requests for updates
   - Configured in repository settings

2. **Review security reports**
   - Check GitHub Actions artifacts
   - Review security job outputs
   - Address warnings promptly

3. **Subscribe to security advisories**
   - GitHub security advisories
   - PyPI security notifications
   - Flask security announcements

### Log Monitoring

```python
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('app.log'),
        logging.StreamHandler()
    ]
)

# Log security events
logger = logging.getLogger(__name__)
logger.warning(f"Unauthorized access attempt from {request.remote_addr}")
```

## Resources

### Tools

- [Bandit](https://bandit.readthedocs.io/)
- [pip-audit](https://pypi.org/project/pip-audit/)
- [Safety](https://pyup.io/safety/)
- [OWASP ZAP](https://www.zaproxy.org/)

### References

- [OWASP Top 10](https://owasp.org/www-project-top-ten/)
- [Python Security Best Practices](https://python.readthedocs.io/en/stable/library/security_warnings.html)
- [Flask Security](https://flask.palletsprojects.com/en/stable/security/)
- [GitHub Security Best Practices](https://docs.github.com/en/code-security)

---

*Last Updated: 2025-11-12*
