[CmdletBinding()]
param(
    [string]$Compiler = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$sourcePath = Join-Path $repoRoot "mr_mc240n_usb_cli.c"
$outputDirectory = Join-Path $repoRoot "bin"
$outputPath = Join-Path $outputDirectory "mr_mc240n_usb.exe"

if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Source file not found: $sourcePath"
}

if (-not $Compiler -and $env:TCC_PATH) {
    $Compiler = $env:TCC_PATH
}

if (-not $Compiler) {
    $localCompiler = Join-Path $repoRoot "tools\tcc-win32\tcc\tcc.exe"
    if (Test-Path -LiteralPath $localCompiler -PathType Leaf) {
        $Compiler = $localCompiler
    }
}

if (-not $Compiler) {
    $pathCompiler = Get-Command "tcc.exe" -ErrorAction SilentlyContinue
    if ($pathCompiler) {
        $Compiler = $pathCompiler.Source
    }
}

if (-not $Compiler -or -not (Test-Path -LiteralPath $Compiler -PathType Leaf)) {
    throw (
        "A 32-bit Tiny C Compiler was not found. Pass -Compiler PATH, set " +
        "TCC_PATH, install tcc.exe on PATH, or place the local tool at " +
        "tools\tcc-win32\tcc\tcc.exe."
    )
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

& $Compiler -m32 -Wall -Werror -o $outputPath $sourcePath
if ($LASTEXITCODE -ne 0) {
    throw "USB bridge build failed with exit code $LASTEXITCODE."
}

Write-Host "Built: $outputPath"
