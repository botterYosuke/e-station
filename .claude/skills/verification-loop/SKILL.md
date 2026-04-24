---
name: verification-loop
description: "A comprehensive verification system for Claude Code sessions (Rust / flowsurface)."
origin: ECC
---

# Verification Loop Skill

A comprehensive verification system for Claude Code sessions.
Customized for the **flowsurface** Rust project.

## When to Use

Invoke this skill:
- After completing a feature or significant code change
- Before creating a PR
- When you want to ensure quality gates pass
- After refactoring

## Verification Phases

### Phase 1: Build Verification
```bash
cargo build 2>&1 | tail -30
```

If build fails, STOP and fix before continuing.

### Phase 2: Compile Check (fast)
```bash
cargo check 2>&1 | head -50
```

Reports all compile errors. Fix all errors before continuing.

### Phase 2.5: Format Check
```bash
cargo fmt --check 2>&1
```

Non-zero exit means formatting issues exist. Run `cargo fmt` to fix, then re-check.

### Phase 3: Lint Check
```bash
cargo clippy -- -D warnings 2>&1 | head -50
```

All clippy warnings are treated as errors (`-D warnings`). Fix all warnings before continuing.

### Phase 4: Test Suite
```bash
cargo test 2>&1 | tail -50
```

Report:
- Total tests: X
- Passed: X
- Failed: X
- Coverage target: 80% minimum (logic/conversion/error paths; GUI rendering exempt)

### Phase 5: Security Scan
```bash
grep -rn "api_key\|secret\|password\|token" --include="*.rs" src/ 2>/dev/null | head -10
```

Review any matches for unintended credential exposure.

### Phase 6: Diff Review
```bash
git diff --stat
git diff HEAD~1 --name-only
```

Review each changed file for:
- Unintended changes
- Missing error handling
- Potential edge cases

## Output Format

After running all phases, produce a verification report:

```
VERIFICATION REPORT
==================

Build:    [PASS/FAIL]
Check:    [PASS/FAIL] (X errors)
Format:   [PASS/FAIL]
Lint:     [PASS/FAIL] (X warnings)
Tests:    [PASS/FAIL] (X/Y passed)
Security: [PASS/FAIL] (X issues)
Diff:     [X files changed]

Overall:  [READY/NOT READY] for PR

Issues to Fix:
1. ...
2. ...
```

**Overall is READY only when**: build errors = 0, clippy warnings = 0, test failures = 0, format issues = 0.

## Continuous Mode

Set a mental checkpoint:
- After completing each function or module
- Before moving to the next task
- Before switching to a new feature

Run: `/verification-loop`

## Integration with Hooks

This skill complements PostToolUse hooks but provides deeper verification.
Hooks catch issues immediately; this skill provides comprehensive review.