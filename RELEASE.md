# Release the Memvora CLI

Use this when the CLI should install on any PC with:

```powershell
python -m pip install memvora
```

## 1. Choose the package source

PyPI install:

```powershell
python -m pip install memvora
```

GitHub install before PyPI:

```powershell
python -m pip install "memvora @ git+https://github.com/YOUR_ORG/memvora.git"
```

## 2. Build the package

```powershell
python -m pip install --upgrade build twine
python -m build
```

This creates files in `dist/`.

## 3. Test publish first

```powershell
python -m twine upload --repository testpypi dist/*
python -m pip install --index-url https://test.pypi.org/simple/ memvora
python -m memvora_cli --version
```

## 4. Publish to PyPI

The first upload for a new package usually needs a PyPI account-wide API token. After the project exists on PyPI, create a project-scoped token for future releases.

```powershell
python -m twine upload dist/*
```

From this CLI repo, run the helper script. It prompts for the API token locally:

```powershell
.\scripts\publish.ps1 -Repository pypi
```

After this, users can install from any PC:

```powershell
python -m pip install memvora
python -m memvora_cli auth --token TOKEN_FROM_WEBSITE --api-url https://api.your-domain.com
```

## 5. Backend requirement

The CLI needs a public FastAPI URL. `http://127.0.0.1:8000` only works on the same computer running the backend.
