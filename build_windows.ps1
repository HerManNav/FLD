$ErrorActionPreference = "Stop"

$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $repo ".venv-build\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Build environment missing. Create .venv-build with Python 3.15.2 and install requirements-build.txt."
}

$version = & $python -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
if ($version -ne "3.15.2") {
    throw "Expected Python 3.15.2 in .venv-build; found $version."
}
$architecture = & $python -c "import struct; print(struct.calcsize('P') * 8)"
if ($architecture -ne "64") {
    throw "Expected 64-bit Python in .venv-build; found $architecture-bit."
}
$pyinstaller = & $python -c "import PyInstaller; print(PyInstaller.__version__)"
if ($pyinstaller -ne "6.22.3") {
    throw "Expected PyInstaller 6.22.3 in .venv-build; found $pyinstaller."
}

Push-Location $repo
try {
    & $python -m PyInstaller --clean --noconfirm FLD.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE."
    }
} finally {
    Pop-Location
}
