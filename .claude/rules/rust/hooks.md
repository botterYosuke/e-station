---
paths:
  - "**/*.rs"
  - "**/Cargo.toml"
---
# Rust Hooks

## PostToolUse Hooks

Configure in `.claude/settings.json` (project-level):

- **cargo fmt**: Auto-format `.rs` files after edit (PostToolUse: Edit|Write on `*.rs`)
- **cargo check**: Verify compilation after changes (PostToolUse: Edit|Write on `*.rs` / `*Cargo.toml`)

`cargo clippy` は自動フックに含まれていない。手動で実行すること:

```bash
cargo clippy -- -D warnings
```
