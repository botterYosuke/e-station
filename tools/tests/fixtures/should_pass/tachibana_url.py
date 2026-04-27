# This file is an ALLOWLISTED replica of the real tachibana_url.py layout.
# It contains the forbidden pattern (kabuka.e-shiten.jp / BASE_URL_PROD =)
# but is excluded from scanning via secret_scan_allowlist.txt pointing at the
# real file.  The meta-test (test_secret_scan.sh) verifies that when the
# scanner is invoked with ONLY this file as input — i.e. a repo where the only
# hit is the allowlisted file — the scanner returns exit 0.
#
# NOTE: this fixture is intentionally named "tachibana_url.py" to mirror the
# allowlist entry.  The scanner uses the absolute path for matching so the
# directory prefix still differs; the test script arranges the paths correctly.

BASE_URL_PROD = "https://kabuka.e-shiten.jp/e_api_v4r8/"
BASE_URL_DEMO = "https://demo-kabuka.e-shiten.jp/e_api_v4r8/"
