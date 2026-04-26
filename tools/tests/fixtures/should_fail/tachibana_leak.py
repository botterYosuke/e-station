# This file MUST trigger secret_scan — it contains patterns that are
# forbidden outside the allowlist.  Used by tools/tests/test_secret_scan.sh
# and tools/tests/test_secret_scan.ps1 to verify the scanner returns exit 1.

# Forbidden: production host literal
BASE_URL = "https://kabuka.e-shiten.jp/e_api_v4r8/"

# Forbidden: credential field names (bare assignment — matches \bsUserId\b\s*[:=])
sUserId = "user123"
sPassword = "secret"

# Forbidden: second password field name
sSecondPassword = "code123"

# Forbidden: BASE_URL_PROD assignment
BASE_URL_PROD = "https://kabuka.e-shiten.jp/e_api_v4r8/"
