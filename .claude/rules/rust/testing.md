---
paths:
  - "**/*.rs"
---
# Rust Testing

## Test Framework

- **`#[test]`** with `#[cfg(test)]` modules for unit tests
- **`#[tokio::test]`** for async tests
- **mockito** for HTTP server mocking (dev-dependency in `exchange` crate)
- **`rstest`** for parameterized tests (workspace dev-dependency)

> `proptest`・`mockall`・`cargo-llvm-cov` は導入済み（dev-dependencies）。

## Test Organization

```text
my_crate/
├── src/
│   ├── lib.rs           # Unit tests in #[cfg(test)] modules
│   └── orders/
│       └── service.rs   # #[cfg(test)] mod tests { ... }
├── tests/               # Integration tests (each file = separate binary)
│   ├── api_test.rs
│   └── common/          # Shared test utilities
│       └── mod.rs
```

Unit tests go inside `#[cfg(test)]` modules in the same file. Integration tests go in `tests/`.

## Unit Test Pattern

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn creates_user_with_valid_email() {
        let user = User::new("Alice", "alice@example.com").unwrap();
        assert_eq!(user.name, "Alice");
    }

    #[test]
    fn rejects_invalid_email() {
        let result = User::new("Bob", "not-an-email");
        assert!(result.is_err());
        assert!(result.unwrap_err().to_string().contains("invalid email"));
    }

    // Result を返すテスト — ? で簡潔に書ける
    #[test]
    fn parses_valid_input() -> Result<(), Box<dyn std::error::Error>> {
        let config = parse_config(r#"{"port": 8080}"#)?;
        assert_eq!(config.port, 8080);
        Ok(())
    }
}
```

## Parameterized Tests with `rstest`

同じロジックを複数の入力で検証する場合に使う。

```rust
use rstest::rstest;

#[rstest]
#[case("hello", 5)]
#[case("", 0)]
#[case("rust", 4)]
fn test_string_length(#[case] input: &str, #[case] expected: usize) {
    assert_eq!(input.len(), expected);
}
```

fixture（共通セットアップ）も使える：

```rust
use rstest::{fixture, rstest};

#[fixture]
fn default_config() -> Config {
    Config::test_default()
}

#[rstest]
fn uses_default_port(default_config: Config) {
    assert_eq!(default_config.port, 8080);
}
```

## Async Tests

```rust
#[tokio::test]
async fn fetches_data_successfully() {
    let client = TestClient::new().await;
    let result = client.get("/data").await;
    assert!(result.is_ok());
}

// タイムアウトのテスト
#[tokio::test]
async fn handles_timeout() {
    use std::time::Duration;
    let result = tokio::time::timeout(
        Duration::from_millis(100),
        slow_operation(),
    ).await;
    assert!(result.is_err(), "should have timed out");
}
```

## Error and Panic Testing

```rust
// Result の検証（#[should_panic] より preferred）
#[test]
fn returns_error_for_invalid_input() {
    let result = parse_config("}{invalid");
    assert!(result.is_err());
    assert!(matches!(result.unwrap_err(), ConfigError::ParseError(_)));
}

// panic が不可避な場合のみ should_panic を使う
#[test]
#[should_panic(expected = "index out of bounds")]
fn panics_on_out_of_bounds() {
    let v: Vec<i32> = vec![];
    let _ = v[0];
}
```

## Test Naming

Use descriptive names that explain the scenario:
- `creates_user_with_valid_email()`
- `rejects_order_when_insufficient_stock()`
- `returns_none_when_not_found()`

## Best Practices

**DO:**
- Test behavior, not implementation
- Use `assert_eq!` over `assert!` for better error messages
- Use `?` in tests that return `Result` for cleaner error output
- Keep tests independent — no shared mutable state

**DON'T:**
- Use `sleep()` in tests — use channels, barriers, or `tokio::time::pause()`
- Use `#[should_panic]` when you can test `Result::is_err()` instead
- Skip error path testing

## Property-Based Testing with `proptest`

ランダム入力でプロパティ（不変条件）を検証する。エッジケースの自動探索に有効。

```rust
use proptest::prelude::*;

proptest! {
    #[test]
    fn encode_decode_roundtrip(input in ".*") {
        let encoded = encode(&input);
        let decoded = decode(&encoded).unwrap();
        prop_assert_eq!(input, decoded);
    }

    #[test]
    fn sort_preserves_length(mut vec in prop::collection::vec(any::<i32>(), 0..100)) {
        let original_len = vec.len();
        vec.sort();
        prop_assert_eq!(vec.len(), original_len);
    }
}
```

カスタム Strategy（入力生成ロジック）も定義できる：

```rust
fn valid_price() -> impl Strategy<Value = f64> {
    (0.0001_f64..1_000_000.0_f64)
}

proptest! {
    #[test]
    fn price_never_negative(price in valid_price()) {
        let tick = Tick::new(price);
        prop_assert!(tick.price() > 0.0);
    }
}
```

## Mocking with `mockall`

トレイトを実装するモックを自動生成し、呼び出し回数・引数・戻り値を検証する。

```rust
use mockall::{automock, predicate::eq};

#[automock]
trait DataRepository {
    fn fetch_ticks(&self, symbol: &str) -> Vec<Tick>;
    fn save(&self, tick: &Tick) -> Result<(), StorageError>;
}

#[test]
fn service_fetches_ticks() {
    let mut mock = MockDataRepository::new();
    mock.expect_fetch_ticks()
        .with(eq("BTCUSDT"))
        .times(1)
        .returning(|_| vec![Tick::default()]);

    let service = TickService::new(Box::new(mock));
    let ticks = service.get_ticks("BTCUSDT");
    assert_eq!(ticks.len(), 1);
}
```

> `mockall` はトレイトベースの設計でのみ有効。具体型の差し替えには `cfg(test)` フィーチャーゲートや依存注入を使う。

## Coverage

- Target 80%+ line coverage
- `cargo-llvm-cov` で計測する（`cargo install cargo-llvm-cov` で導入）

```bash
cargo llvm-cov                       # Summary
cargo llvm-cov --html                # HTML report（target/llvm-cov/html/）
cargo llvm-cov --fail-under-lines 80 # CI でしきい値チェック
```

## Testing Commands

```bash
cargo test                       # Run all tests
cargo test -- --nocapture        # Show println output
cargo test test_name             # Run tests matching pattern
cargo test --lib                 # Unit tests only
cargo test --test api_test       # Specific integration test (tests/api_test.rs)
cargo test --doc                 # Doc tests only
cargo test --no-fail-fast        # Don't stop on first failure
cargo test -- --ignored          # Run ignored tests
```
