---
paths:
  - "**/*.rs"
---
# Rust Security

## Secrets Management

- Never hardcode API keys, tokens, or credentials in source code
- Use environment variables: `std::env::var("API_KEY")`
- Fail fast if required secrets are missing at startup
- Keep `.env` files in `.gitignore`

```rust
// BAD
const API_KEY: &str = "sk-abc123...";

// GOOD — environment variable with early validation
fn load_api_key() -> anyhow::Result<String> {
    std::env::var("PAYMENT_API_KEY")
        .context("PAYMENT_API_KEY must be set")
}
```

## Input Validation

- Validate all user input at system boundaries before processing
- Use the type system to enforce invariants (newtype pattern)
- Parse, don't validate — convert unstructured data to typed structs at the boundary
- Reject invalid input with clear error messages

## Unsafe Code

- Minimize `unsafe` blocks — prefer safe abstractions
- Every `unsafe` block must have a `// SAFETY:` comment explaining the invariant
- Never use `unsafe` to bypass the borrow checker for convenience
- Audit all `unsafe` code during review — it is a red flag without justification

```rust
// GOOD — safety comment documents ALL required invariants
let widget: &Widget = {
    // SAFETY: `ptr` is non-null, aligned, points to an initialized Widget,
    // and no mutable references or mutations exist for its lifetime.
    unsafe { &*ptr }
};

// BAD — no safety justification
unsafe { &*ptr }
```

## Dependency Security

- Run `cargo audit` to scan for known CVEs in dependencies
- Run `cargo deny check` for license and advisory compliance
- Use `cargo tree` to audit transitive dependencies
- Minimize dependency count — evaluate before adding new crates

```bash
# Security audit
cargo audit

# Inspect dependency tree
cargo tree
cargo tree -d  # Show duplicates only
```

## Error Messages

- Never expose internal paths, stack traces, or database errors in API responses
- Log detailed errors server-side; return generic messages to clients
- Use `tracing` or `log` for structured server-side logging