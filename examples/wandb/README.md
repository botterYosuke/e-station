# examples/wandb/ -- Flowsurface W&B Integration

This directory contains standalone scripts for uploading Flowsurface replay results
to [Weights & Biases](https://wandb.ai). None of these scripts are imported by the
Flowsurface core (`python/engine/`, `src/`, `engine-client/`).

## Prerequisites

- `uv` installed (`pip install uv` or see https://docs.astral.sh/uv/getting-started/installation/)
- W&B account (free at https://wandb.ai)
- W&B authenticated (see [Authentication](#authentication) below)

If `uv` is not installed, Flowsurface will show an error:
> "uv が見つかりません。https://docs.astral.sh/uv/ からインストールしてください。"

## Authentication

W&B credentials are resolved in this priority order:

1. `WANDB_API_KEY` environment variable (CI / temporary use)
2. `~/.netrc` (Windows: `%USERPROFILE%\_netrc`) entry for `machine api.wandb.ai`

The recommended way to set up persistent credentials is via the GUI:
**Tools > W&B にログイン...** -- this runs `wandb login` which writes to `~/.netrc`.

You can check your current authentication status manually:

```bash
uv run --with wandb python examples/wandb/check_auth.py
# Prints a single JSON line, e.g.:
# {"authenticated": true, "method": "netrc", "username": "alice", "error": null}
```

## check_auth.py

Authentication status checker. Used by the Flowsurface GUI to determine whether to
enable the "W&B に登録..." menu item.

```bash
uv run --with wandb python examples/wandb/check_auth.py
```

Output (always exits 0, JSON on stdout):

```json
{"authenticated": true,  "method": "netrc", "username": "alice", "error": null}
{"authenticated": true,  "method": "env",   "username": null,    "error": null}
{"authenticated": false, "method": "none",  "username": null,    "error": null}
```

The API key value is never printed to stdout.

## submit_run.py

Uploads a completed replay RunBuffer directory to W&B. Called automatically by the
GUI when you choose **Tools > W&B に登録...**, but can also be run manually:

```bash
uv run --with wandb python examples/wandb/submit_run.py \
    --run-buffer "%APPDATA%\flowsurface\run-buffer\1714800123-buy_and_hold-1301_TSE" \
    --project flowsurface-strategies \
    --run-name "buy_and_hold @ 1301.TSE 2025-01-06..2025-03-31" \
    --tags replay,buy_and_hold
```

The final line of stdout is `URL: <wandb_run_url>` (parsed by the Rust GUI to display
a clickable link in the submission modal).

**Exit codes:**

| Code | Meaning |
|------|---------|
| 0 | Success |
| 2 | Auth error (not logged in) |
| 3 | Rate limit |
| 4 | Network error |
| 5 | Server 5xx |
| 6 | Partial failure (some rows skipped) |

## pii_scrub.py

PII scrubber used by `submit_run.py` as a final upload-time sanity check.
Forbidden keys (account IDs, tokens, credentials) are stripped before any data
reaches W&B. This is an independent copy -- no dependency on `python/engine/`.

```python
from pii_scrub import scrub, assert_no_forbidden_keys, FILLS_ALLOWED_KEYS

clean = scrub(event_dict, FILLS_ALLOWED_KEYS)
assert_no_forbidden_keys(clean, FILLS_ALLOWED_KEYS)  # raises ValueError if violated
```

## Running tests

The tests use monkeypatching so `wandb` does **not** need to be installed:

```bash
uv run pytest examples/wandb/tests/ -v
```

## Core contamination rule

`import wandb` and `import weave` are only permitted inside `examples/wandb/`.
They must never appear in `python/engine/`, `src/`, or `engine-client/`.

```bash
# Verify no contamination
grep -rn "import wandb" python/engine/ src/ engine-client/
# Must return no output
```
