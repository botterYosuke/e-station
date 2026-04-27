# tools/secret_scan.ps1 — Tachibana secret / prod-URL leak scanner (T7, F-L1)
# Windows/PowerShell equivalent of tools/secret_scan.sh
#
# Reads patterns from tools/secret_scan_patterns.txt (one regex per line;
# blank lines and lines starting with '#' are ignored).
# Reads file-level allowlist from tools/secret_scan_allowlist.txt.
#
# Exit codes:
#   0  No disallowed matches found
#   1  One or more disallowed matches found (details printed to stderr)
#   2  Usage / configuration error

param(
    [string]$RepoRoot = ""
)

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..")).Path
}

$PatternsFile = Join-Path $ScriptDir "secret_scan_patterns.txt"
$AllowlistFile = Join-Path $ScriptDir "secret_scan_allowlist.txt"

if (-not (Test-Path $PatternsFile)) {
    Write-Error "ERROR: patterns file not found: $PatternsFile"
    exit 2
}

# Load allowlist (paths relative to repo root)
$Allowlist = @()
if (Test-Path $AllowlistFile) {
    foreach ($line in (Get-Content $AllowlistFile)) {
        $line = $line.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        # Normalise to absolute path using OS separator
        $abs = Join-Path $RepoRoot ($line.Replace("/", [System.IO.Path]::DirectorySeparatorChar))
        $Allowlist += $abs
    }
}

# Directories excluded from scanning
$ExcludedDirs = @(".git", ".venv", "target", ".claude", "node_modules", "docs", "__pycache__", ".pytest_cache")

$Found = $false

foreach ($rawPattern in (Get-Content $PatternsFile)) {
    $pattern = $rawPattern.Trim()
    if (-not $pattern -or $pattern.StartsWith("#")) { continue }

    # Get all files in the repo (excluding certain directories)
    $files = Get-ChildItem -Path $RepoRoot -Recurse -File | Where-Object {
        $parts = $_.FullName.Substring($RepoRoot.Length + 1).Split([System.IO.Path]::DirectorySeparatorChar)
        $skip = $false
        foreach ($excl in $ExcludedDirs) {
            if ($parts -contains $excl) { $skip = $true; break }
        }
        -not $skip
    }

    foreach ($file in $files) {
        # Check allowlist
        $inAllowlist = $false
        foreach ($al in $Allowlist) {
            if ($file.FullName -eq $al) { $inAllowlist = $true; break }
        }
        if ($inAllowlist) { continue }

        # Search for pattern in file
        try {
            $matches = Select-String -Path $file.FullName -Pattern $pattern -ErrorAction SilentlyContinue
            foreach ($m in $matches) {
                $rel = $m.Path.Substring($RepoRoot.Length + 1)
                Write-Host "FAIL: secret pattern /$pattern/ matched in ${rel}:$($m.LineNumber): $($m.Line.Trim())" -ForegroundColor Red
                $Found = $true
            }
        } catch {
            # Binary files etc. — skip silently
        }
    }
}

if (-not $Found) {
    Write-Host "secret_scan: OK (no disallowed patterns found)" -ForegroundColor Green
    exit 0
} else {
    exit 1
}
