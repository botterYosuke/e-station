# tools/tests/test_secret_scan.ps1 — meta-tests for tools/secret_scan.ps1 (HIGH-D6)
# Windows/PowerShell equivalent of test_secret_scan.sh.
#
# Exit codes: 0 = all pass, 1 = any failure.

param()

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$Scanner = Join-Path $RepoRoot "tools\secret_scan.ps1"
$FixturesFail = Join-Path $ScriptDir "fixtures\should_fail"
$FixturesPass = Join-Path $ScriptDir "fixtures\should_pass"

$PassCount = 0
$FailCount = 0

function Invoke-Scanner {
    # Invoke secret_scan.ps1 as a subprocess and return its exit code.
    # Uses the call operator (&) instead of Start-Process to avoid the
    # PowerShell 5.1 restriction that forbids combining -NoNewWindow with
    # -RedirectStandardOutput/-RedirectStandardError.
    param(
        [string]$ScriptPath,
        [string[]]$ExtraArgs = @()
    )
    try {
        & powershell.exe -NonInteractive -File $ScriptPath @ExtraArgs 2>$null | Out-Null
        return $LASTEXITCODE
    } catch {
        return 99
    }
}

# ── Test 1: should_fail fixture triggers exit 1 ──────────────────────────────
$TmpFail = New-TemporaryFile | ForEach-Object { Remove-Item $_; New-Item -Type Directory $_.FullName } | Select-Object -ExpandProperty FullName
try {
    Copy-Item "$FixturesFail\*" $TmpFail -Recurse

    # Create a minimal tools dir so the scanner can find pattern files
    $toolsDir = New-Item -Type Directory (Join-Path $TmpFail "tools")
    Copy-Item (Join-Path $RepoRoot "tools\secret_scan_patterns.txt") (Join-Path $toolsDir "secret_scan_patterns.txt")
    Copy-Item (Join-Path $RepoRoot "tools\secret_scan_allowlist.txt") (Join-Path $toolsDir "secret_scan_allowlist.txt")

    # Run the scanner with TmpFail as RepoRoot
    $actual = Invoke-Scanner -ScriptPath $Scanner -ExtraArgs @("-RepoRoot", $TmpFail)

    if ($actual -eq 1) {
        Write-Host "PASS: should_fail fixture causes exit 1 (exit $actual)" -ForegroundColor Green
        $PassCount++
    } else {
        Write-Host "FAIL: should_fail fixture causes exit 1 — expected 1, got $actual" -ForegroundColor Red
        $FailCount++
    }
} finally {
    Remove-Item $TmpFail -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Test 2: allowlisted file causes exit 0 ───────────────────────────────────
$TmpPass = New-TemporaryFile | ForEach-Object { Remove-Item $_; New-Item -Type Directory $_.FullName } | Select-Object -ExpandProperty FullName
try {
    $allowlisted = New-Item -Type Directory (Join-Path $TmpPass "python\engine\exchanges") -Force
    Copy-Item (Join-Path $FixturesPass "tachibana_url.py") (Join-Path $allowlisted "tachibana_url.py")

    $toolsDir2 = New-Item -Type Directory (Join-Path $TmpPass "tools")
    Copy-Item (Join-Path $RepoRoot "tools\secret_scan_patterns.txt") (Join-Path $toolsDir2 "secret_scan_patterns.txt")
    Copy-Item (Join-Path $RepoRoot "tools\secret_scan_allowlist.txt") (Join-Path $toolsDir2 "secret_scan_allowlist.txt")

    $actual = Invoke-Scanner -ScriptPath $Scanner -ExtraArgs @("-RepoRoot", $TmpPass)

    if ($actual -eq 0) {
        Write-Host "PASS: allowlisted file causes exit 0 (exit $actual)" -ForegroundColor Green
        $PassCount++
    } else {
        Write-Host "FAIL: allowlisted file causes exit 0 — expected 0, got $actual" -ForegroundColor Red
        $FailCount++
    }
} finally {
    Remove-Item $TmpPass -Recurse -Force -ErrorAction SilentlyContinue
}

# ── Test 3: actual repo scan must pass ───────────────────────────────────────
$actual = Invoke-Scanner -ScriptPath $Scanner -ExtraArgs @("-RepoRoot", $RepoRoot)

if ($actual -eq 0) {
    Write-Host "PASS: actual repo scan returns exit 0 (exit $actual)" -ForegroundColor Green
    $PassCount++
} else {
    Write-Host "FAIL: actual repo scan returned exit $actual (expected 0)" -ForegroundColor Red
    $FailCount++
}

# ── Summary ──────────────────────────────────────────────────────────────────
Write-Host "test_secret_scan.ps1: $PassCount passed, $FailCount failed"
if ($FailCount -gt 0) { exit 1 }
exit 0
