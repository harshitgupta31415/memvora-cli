param(
  [ValidateSet("pypi", "testpypi")]
  [string]$Repository = "pypi"
)

$ErrorActionPreference = "Stop"

$PackageDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$VenvPython = Join-Path $PackageDir ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

Push-Location $PackageDir
try {
  foreach ($Path in @("dist", "build", "src\memvora_cli.egg-info")) {
    if (Test-Path $Path) {
      Remove-Item -LiteralPath $Path -Recurse -Force
    }
  }

  & $Python -m pip install --upgrade build twine
  & $Python -m build
  & $Python -m twine check "dist\*"

  $env:TWINE_USERNAME = "__token__"
  if (-not $env:TWINE_PASSWORD) {
    $SecureToken = Read-Host "Paste PyPI API token for $Repository" -AsSecureString
    $TokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureToken)
    try {
      $env:TWINE_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($TokenPtr)
    }
    finally {
      [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($TokenPtr)
    }
  }

  & $Python -m twine upload --repository $Repository "dist\*"
}
finally {
  Pop-Location
}
