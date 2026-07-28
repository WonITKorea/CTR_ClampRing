$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    python -m py_compile main.py hardware.py mr_mc240n_pcie_check.py
    if ($LASTEXITCODE -ne 0) {
        throw "Python syntax validation failed."
    }

    git diff --check
    if ($LASTEXITCODE -ne 0) {
        throw "Git whitespace validation failed."
    }

    git diff --cached --check
    if ($LASTEXITCODE -ne 0) {
        throw "Staged Git whitespace validation failed."
    }

    $repositoryFiles = @(
        git ls-files --cached --others --exclude-standard
    )
    $forbiddenFiles = @(
        $repositoryFiles | Where-Object {
            $_ -match '(?i)\.(dll|exe|lib|pyd|sys|chm)$'
        }
    )
    if ($forbiddenFiles.Count -gt 0) {
        throw (
            "Vendor or generated binary is not ignored: " +
            ($forbiddenFiles -join ", ")
        )
    }

    Write-Host "Repository validation passed."
}
finally {
    Pop-Location
}
