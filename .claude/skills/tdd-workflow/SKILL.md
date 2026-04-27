---
name: tdd-workflow
description: Use this skill when writing new features, fixing bugs, or refactoring code. Enforces test-driven development with 80%+ coverage including unit, integration, and E2E tests.
origin: ECC (customized for e-station)
---

# Test-Driven Development Workflow — e-station (Rust + Python)

このスキルは e-station プロジェクト（Rust + Iced GUI、ワークスペース構成）に特化した TDD フローを提供する。

## When to Activate

- 新機能・ロジックの追加
- バグ修正
- リファクタリング
- 取引所アダプター追加
- リプレイ・チャート機能の変更

---

## Workspace Structure

```
flowsurface/               # GUI・チャート・リプレイ (src/)
flowsurface-exchange/      # 取引所アダプター (exchange/src/)
flowsurface-data/          # データ集約・設定永続化 (data/src/)
```

テストは**変更したクレート**内に書く。クレートをまたぐ結合テストは `exchange/tests/` 等の統合テストディレクトリへ。

---

## Core Principles

### 1. Tests BEFORE Code
テストを先に書き、失敗させてから実装する。Red → Green → Refactor。

### 2. Test Placement
| テスト種別 | 場所 |
|---|---|
| ユニットテスト | モジュールファイル内 `#[cfg(test)] mod tests { }` |
| 統合テスト | `<crate>/tests/<name>.rs` |
| E2E テスト | `.claude/skills/e2e-test/` スキルを使用 |

### 3. Coverage Goal
ロジック・変換・エラーパスは 80% 以上。GUI レンダリング部分はユニットテスト対象外で可。

---

## TDD Workflow Steps

### Step 1: ユーザーシナリオを言語化
```
対象クレート: flowsurface-exchange
シナリオ: Bybit の WebSocket が切断されたとき、
          自動再接続して購読を復元できる
```

### Step 2: テストケースを列挙（まず failing test から）

```rust
// exchange/src/bybit/adapter.rs
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reconnect_restores_subscriptions() {
        // Arrange: アダプターを接続状態に設定
        // Act: 切断イベントをシミュレート
        // Assert: 再接続後に購読が復元されている
        todo!("implement after test is confirmed failing")
    }

    #[test]
    fn reconnect_emits_error_after_max_retries() {
        todo!()
    }
}
```

### Step 3: テストを実行して失敗を確認
```bash
cargo test -p flowsurface-exchange reconnect
# FAIL が出ることを確認（コンパイルエラーは OK）
```

### Step 4: 最小限の実装
テストが green になる最小のコードを書く。過剰な抽象化は禁止。

### Step 5: テストを再実行
```bash
cargo test -p flowsurface-exchange
# すべて PASS になること
```

### Step 6: リファクタリング
テストを green に保ちながらコード品質を向上させる。

### Step 7: カバレッジ確認
```bash
cargo llvm-cov --package flowsurface-exchange --html
# target/llvm-cov/html/index.html を確認
```

---

## Testing Patterns

### ユニットテスト（同期）
```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_kline_from_binance_response() {
        // Arrange
        let raw = r#"{"t":1700000000000,"o":"100.0","h":"101.0","l":"99.0","c":"100.5","v":"500.0"}"#;

        // Act
        let kline: Kline = serde_json::from_str(raw).expect("should parse");

        // Assert
        assert_eq!(kline.open, 100.0);
        assert_eq!(kline.high, 101.0);
        assert_eq!(kline.volume, 500.0);
    }

    #[test]
    fn parse_returns_error_on_missing_field() {
        let raw = r#"{"t":1700000000000}"#;
        let result: Result<Kline, _> = serde_json::from_str(raw);
        assert!(result.is_err());
    }
}
```

### 非同期テスト（tokio）
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use tokio::time::{timeout, Duration};

    #[tokio::test]
    async fn fetch_historical_klines_returns_sorted_data() {
        // Arrange
        let adapter = BinanceAdapter::new_test();

        // Act
        let klines = adapter
            .fetch_klines("BTCUSDT", Timeframe::M1, 100)
            .await
            .expect("fetch should succeed");

        // Assert
        assert!(!klines.is_empty());
        assert!(klines.windows(2).all(|w| w[0].open_time <= w[1].open_time));
    }

    #[tokio::test]
    async fn fetch_times_out_gracefully() {
        let adapter = BinanceAdapter::new_test();
        let result = timeout(Duration::from_millis(10), adapter.fetch_klines("X", Timeframe::M1, 1)).await;
        // タイムアウトまたはエラーになること
        assert!(result.is_err() || result.unwrap().is_err());
    }
}
```

### HTTP モック（mockito）
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use mockito::Server;

    #[tokio::test]
    async fn rest_client_handles_rate_limit_response() {
        // Arrange
        let mut server = Server::new_async().await;
        let mock = server
            .mock("GET", "/api/v3/klines")
            .with_status(429)
            .with_header("Retry-After", "1")
            .create_async()
            .await;

        let client = BinanceRestClient::new(&server.url());

        // Act
        let result = client.get_klines("BTCUSDT", "1m", 100).await;

        // Assert
        assert!(matches!(result, Err(ExchangeError::RateLimit { .. })));
        mock.assert_async().await;
    }

    #[tokio::test]
    async fn rest_client_parses_valid_response() {
        let mut server = Server::new_async().await;
        let body = include_str!("../fixtures/binance_klines.json");
        let mock = server
            .mock("GET", "/api/v3/klines")
            .with_status(200)
            .with_body(body)
            .create_async()
            .await;

        let client = BinanceRestClient::new(&server.url());
        let result = client.get_klines("BTCUSDT", "1m", 5).await;

        assert!(result.is_ok());
        assert_eq!(result.unwrap().len(), 5);
        mock.assert_async().await;
    }
}
```

### 統合テスト（`tests/` ディレクトリ）
```rust
// exchange/tests/tachibana_integration.rs
// 実際の API を叩く統合テスト — .env にクレデンシャルが必要
// 通常の cargo test では skip し、明示的に実行する

use std::env;

async fn load_credentials() -> Option<(String, String)> {
    dotenvy::dotenv().ok();
    let user = env::var("TACHIBANA_USER").ok()?;
    let pass = env::var("TACHIBANA_PASS").ok()?;
    Some((user, pass))
}

#[tokio::test]
#[ignore = "requires live credentials in .env"]
async fn login_returns_session_token() {
    let Some((user, pass)) = load_credentials().await else {
        eprintln!("skipping: no credentials");
        return;
    };
    // ... 実際のテスト
}
```

実行方法:
```bash
# 通常テスト（ignore なし）
cargo test -p flowsurface-exchange

# 統合テスト含む
cargo test -p flowsurface-exchange -- --include-ignored
```

---

## テストファイル配置

```
exchange/
├── src/
│   ├── binance/
│   │   ├── adapter.rs          # #[cfg(test)] mod tests { } を末尾に
│   │   └── rest.rs
│   └── tachibana/
│       ├── adapter.rs
│       └── fixtures/           # テスト用 JSON ファイル
│           └── klines.json
└── tests/
    └── tachibana_integration.rs  # 統合テスト（要クレデンシャル）

data/
└── src/
    └── aggregator.rs           # #[cfg(test)] mod tests { }

src/
└── replay/
    └── controller.rs           # #[cfg(test)] mod tests { }
```

---

## よくある間違いと正しい書き方

### WRONG: 実装詳細をテスト
```rust
// 内部フィールドを直接検証しない
assert_eq!(adapter.retry_count, 3);
```

### CORRECT: 振る舞いをテスト
```rust
// 外部から観測できる結果を検証する
let result = adapter.connect().await;
assert!(matches!(result, Err(ExchangeError::MaxRetriesExceeded)));
```

### WRONG: テスト間で状態を共有
```rust
static mut SHARED_CLIENT: Option<Client> = None; // 危険
```

### CORRECT: 各テストで独立したセットアップ
```rust
fn make_client() -> Client {
    Client::new_with_config(TestConfig::default())
}

#[test]
fn test_a() { let c = make_client(); /* ... */ }

#[test]
fn test_b() { let c = make_client(); /* ... */ }
```

### WRONG: panic! でアサーション
```rust
if result.is_err() { panic!("failed"); }
```

### CORRECT: assert! / assert_eq! / unwrap_or_else
```rust
let klines = result.expect("fetch_klines should succeed in test");
assert_eq!(klines.len(), 5, "expected exactly 5 klines");
```

---

## テスト実行コマンド早見表

```bash
# 全クレートのテスト
cargo test --workspace

# 特定クレートのみ
cargo test -p flowsurface-exchange

# テスト名フィルター
cargo test parse_kline

# 出力を表示しながら実行
cargo test -- --nocapture

# 並列数制限（テスト競合時）
cargo test -- --test-threads=1

# カバレッジ（cargo-llvm-cov が必要）
cargo llvm-cov --workspace --html

# watch モード（cargo-watch が必要）
cargo watch -x "test -p flowsurface-exchange"
```

---

## E2E テスト

GUI・エンドツーエンドのテストは **e2e-test スキル**を使用すること。
このスキルは HTTP API 経由でアプリを操作する。

```
/e2e-test  # e2e-test スキルを起動
```

---

## CI との連携

```yaml
# .github/workflows/test.yml に追加する場合
- name: Run tests
  run: cargo test --workspace

- name: Clippy
  run: cargo clippy --workspace -- -D warnings

- name: Coverage (optional)
  run: cargo llvm-cov --workspace --lcov --output-path lcov.info
```

---

## Success Metrics

- `cargo test --workspace` が全 PASS
- ロジック部分のカバレッジ 80%+
- `#[ignore]` テストが 0（統合テスト除く）
- `cargo clippy` が warning なし
- 追加したテストが独立して実行できる（順序依存なし）

---

**Remember**: Rust のコンパイラがコンパイルエラーを防いでも、ロジックバグは防がない。テストがその安全網。
