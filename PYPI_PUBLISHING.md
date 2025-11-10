# Publishing OG-AI to PyPI

This guide explains how to publish the OG-AI agent package to PyPI (Python Package Index).

## Prerequisites

1. **PyPI Account**: Create an account at https://pypi.org/account/register/
2. **API Token**: Generate an API token at https://pypi.org/manage/account/token/
3. **Install Build Tools**:
   ```bash
   pip install build twine
   ```

## Option 1: Build and Publish to PyPI

### Step 1: Build the Package

```bash
# Clean any previous builds
rm -rf dist/ build/ *.egg-info

# Build the package
python -m build
```

This creates two files in the `dist/` directory:
- `og-ai-agent-1.0.0.tar.gz` (source distribution)
- `og_ai_agent-1.0.0-py3-none-any.whl` (wheel distribution)

### Step 2: Test the Package Locally

```bash
# Install locally to test
pip install dist/og_ai_agent-1.0.0-py3-none-any.whl

# Test the installation
python -c "from ai_agent import AIAgent; print('Package installed successfully!')"

# Test the CLI
og-ai --cli
```

### Step 3: Upload to TestPyPI (Optional but Recommended)

TestPyPI is a separate instance of PyPI for testing:

```bash
# Upload to TestPyPI first
python -m twine upload --repository testpypi dist/*

# Install from TestPyPI to verify
pip install --index-url https://test.pypi.org/simple/ og-ai-agent
```

### Step 4: Upload to PyPI

```bash
# Upload to PyPI
python -m twine upload dist/*
```

When prompted:
- **Username**: `__token__`
- **Password**: Your PyPI API token (including the `pypi-` prefix)

### Step 5: Verify Installation

After publishing:

```bash
# Install from PyPI
pip install og-ai-agent

# Verify it works
python -c "from ai_agent import AIAgent; print('Success!')"
```

## Option 2: Install from GitHub (No PyPI Upload Needed)

Users can install directly from this GitHub repository:

```bash
# Install latest from main branch
pip install git+https://github.com/Goatfighter206/OG-AI-.git

# Install from specific branch
pip install git+https://github.com/Goatfighter206/OG-AI-.git@copilot/deploy-agent-feature

# Install from specific commit
pip install git+https://github.com/Goatfighter206/OG-AI-.git@5312a47
```

## Using the Package After Installation

### As a Python Module

```python
from ai_agent import AIAgent

# Create an agent
agent = AIAgent(name="My Agent")

# Process messages
response = agent.process_message("Hello!")
print(response)
```

### As a CLI Tool

```bash
# Run interactive mode
og-ai --cli

# Run as server
python -m ai_agent
```

### As a Web Server with Gunicorn

```bash
# Using the installed package
gunicorn ai_agent:app
```

## Updating the Package

When you make changes and want to publish a new version:

1. **Update version** in `pyproject.toml`:
   ```toml
   version = "1.0.1"  # Increment version
   ```

2. **Commit changes**:
   ```bash
   git add .
   git commit -m "Release version 1.0.1"
   git tag v1.0.1
   git push --tags
   ```

3. **Rebuild and republish**:
   ```bash
   rm -rf dist/
   python -m build
   python -m twine upload dist/*
   ```

## Package Information

- **Package Name**: `og-ai-agent`
- **PyPI URL**: https://pypi.org/project/og-ai-agent/ (after publishing)
- **Install Command**: `pip install og-ai-agent`
- **Source Code**: https://github.com/Goatfighter206/OG-AI-

## Files Included in Package

The package includes:
- `ai_agent.py` - Main module
- `config.json` - Default configuration
- `README.md` - Documentation
- `LICENSE` - MIT License

Files excluded (deployment-specific):
- Procfile, Dockerfile, docker-compose.yml
- render.yaml, runtime.txt
- example_usage.py
- .github/, .git/

## Troubleshooting

### "Package already exists" Error

If you get this error, the version already exists on PyPI. Update the version in `pyproject.toml` and rebuild.

### Import Errors

Make sure all dependencies are installed:
```bash
pip install Flask Flask-CORS gunicorn
```

### Build Errors

Ensure you have the latest build tools:
```bash
pip install --upgrade build setuptools wheel
```

## Additional Resources

- [PyPI Documentation](https://pypi.org/help/)
- [Python Packaging Guide](https://packaging.python.org/)
- [Twine Documentation](https://twine.readthedocs.io/)
- [setuptools Documentation](https://setuptools.pypa.io/)

## Security Note

**Never commit your PyPI API token to the repository!** Always use environment variables or secure credential storage.

For automated publishing, consider using GitHub Actions with encrypted secrets.
