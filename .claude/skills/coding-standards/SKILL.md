---
name: coding-standards
description: Rust コーディング規約。命名規則・イミュータビリティ・エラーハンドリング・テスト・コード品質レビューの基準。e-station プロジェクト向け。
origin: ECC
---

# Coding Standards & Best Practices (Rust)

flowsurface プロジェクトの Rust コーディング規約。

## When to Activate

- 新しいモジュールや機能を追加するとき
- コードレビューや品質チェックを行うとき
- 既存コードをリファクタリングするとき
- 命名・構造・規約の確認が必要なとき
- 新しい貢献者へのオンボーディング

## Scope Boundaries

Activate this skill for:
- 命名規則の確認・適用
- イミュータビリティのデフォルト適用
- 可読性、KISS、DRY、YAGNI の適用
- エラーハンドリングの方針確認
- コードの臭い検出

## Code Quality Principles

### 1. Readability First
- コードは書く時間より読む時間の方が長い
- 明確な変数名・関数名を使う
- 自己文書化コードをコメントより優先する
- `cargo fmt` で一貫したフォーマットを維持する

### 2. KISS (Keep It Simple, Stupid)
- 動く最もシンプルな実装を選ぶ
- 過度な設計を避ける
- 早期最適化をしない
- 巧妙なコードより理解しやすいコードを選ぶ

### 3. DRY (Don't Repeat Yourself)
- 共通ロジックは関数に抽出する
- ユーティリティをモジュール間で共有する
- コピペプログラミングを避ける

### 4. YAGNI (You Aren't Gonna Need It)
- 必要になる前に機能を作らない
- 投機的な汎用化を避ける
- 複雑さは実際に必要になったときだけ追加する

---

## Rust 命名規則

```rust
// 関数・変数・モジュール: snake_case
fn fetch_candle_data(market_id: &str) -> Result<Vec<Candle>, Error> { }
let total_volume: f64 = 0.0;
mod chart_renderer;

// 型・トレイト・enum: PascalCase
struct CandleChart { }
trait DataProvider { }
enum TimeFrame { Minutes1, Minutes5, Hours1 }

// 定数: SCREAMING_SNAKE_CASE
const MAX_CANDLES: usize = 1000;
const DEFAULT_TIMEFRAME_MS: u64 = 60_000;

// ライフタイム: 短い小文字
fn longest<'a>(x: &'a str, y: &'a str) -> &'a str { }
```

---

## イミュータビリティ（CRITICAL）

```rust
// PASS: GOOD: デフォルト不変
let price = 42.0_f64;
let candles = vec![candle1, candle2];

// PASS: GOOD: 変更が必要な場合のみ mut
let mut buffer = Vec::new();
buffer.push(candle);

// 関数引数: 変更不要なら共有参照
fn calculate_sma(candles: &[Candle]) -> f64 { }

// 関数引数: 変更が必要なら可変参照
fn normalize_prices(candles: &mut Vec<Candle>) { }

// FAIL: BAD: 不要な mut
let mut price = 42.0_f64;  // 変更しないなら mut は不要
```

---

## エラーハンドリング

```rust
// PASS: GOOD: ? 演算子で伝播
fn load_config(path: &Path) -> Result<Config, ConfigError> {
    let content = fs::read_to_string(path)?;
    let config: Config = toml::from_str(&content)?;
    Ok(config)
}

// PASS: GOOD: thiserror でエラー型を定義
use thiserror::Error;

#[derive(Debug, Error)]
pub enum DataError {
    #[error("IO error: {0}")]
    Io(#[from] std::io::Error),
    #[error("Parse error at line {line}: {msg}")]
    Parse { line: usize, msg: String },
    #[error("Market {market_id} not found")]
    NotFound { market_id: String },
}

// FAIL: BAD: テスト外での unwrap/expect
let data = risky_operation().unwrap();       // テスト外では使用しない
let val = maybe_none.expect("must exist");   // テスト外では使用しない

// PASS: OK: テストコード内での unwrap は許容
#[test]
fn test_parse_candle() {
    let candle = parse("1620000000,100.0,105.0,99.0,102.0").unwrap();
    assert_eq!(candle.close, 102.0);
}
```

---

## 関数設計

```rust
// PASS: GOOD: 単一責任、明確な名前
fn calculate_bollinger_bands(candles: &[Candle], period: usize) -> BollingerBands {
    let sma = calculate_sma(candles, period);
    let std_dev = calculate_std_dev(candles, period, sma);
    BollingerBands { upper: sma + 2.0 * std_dev, middle: sma, lower: sma - 2.0 * std_dev }
}

// FAIL: BAD: 50行を超える関数（分割を検討）
fn process_everything() {
    // 100行の混在したロジック...
}

// PASS: GOOD: 長い関数は小さな関数に分割
fn process_market_data(raw: &RawData) -> ProcessedData {
    let validated = validate_data(raw);
    let normalized = normalize_prices(&validated);
    aggregate_candles(normalized)
}
```

---

## 所有権と借用

```rust
// PASS: GOOD: 所有権が必要な場合のみ move
fn store_candles(candles: Vec<Candle>) {
    self.cache = candles;  // 所有権を取得
}

// PASS: GOOD: 読むだけなら参照を使う
fn display_candles(candles: &[Candle]) {
    for c in candles { println!("{:?}", c); }
}

// PASS: GOOD: Clone は本当に必要な場合のみ
let snapshot = self.candles.clone();  // 意図的なコピーは明示
```

---

## 定数とマジックナンバー

```rust
// FAIL: BAD: マジックナンバー
if retry_count > 3 { }
thread::sleep(Duration::from_millis(500));

// PASS: GOOD: 名前付き定数
const MAX_RETRIES: u32 = 3;
const RECONNECT_DELAY_MS: u64 = 500;

if retry_count > MAX_RETRIES { }
thread::sleep(Duration::from_millis(RECONNECT_DELAY_MS));
```

---

## コメント規則

```rust
// PASS: GOOD: WHY を説明する（WHAT ではない）
// 指数バックオフで API への負荷を避ける
let delay = Duration::from_millis(500 * 2u64.pow(retry_count));

// 意図的に unsafe を使用: FFI コールバックの生存期間を手動管理
unsafe { ... }

// FAIL: BAD: 自明なことをコメントしない
// カウンターを1増やす
count += 1;
```

---

## テスト規則（AAA パターン）

```rust
#[cfg(test)]
mod tests {
    use super::*;

    // PASS: GOOD: 説明的なテスト名
    #[test]
    fn calculate_sma_returns_correct_average_for_five_candles() {
        // Arrange
        let candles = vec![
            candle(100.0), candle(102.0), candle(98.0), candle(101.0), candle(99.0),
        ];

        // Act
        let sma = calculate_sma(&candles, 5);

        // Assert
        assert!((sma - 100.0).abs() < f64::EPSILON);
    }

    // PASS: GOOD: 非同期テスト
    #[tokio::test]
    async fn fetch_market_data_returns_error_on_connection_failure() {
        // Arrange
        let client = MarketClient::new("http://localhost:9999");  // 存在しないポート

        // Act
        let result = client.fetch_candles("BTC-USD").await;

        // Assert
        assert!(result.is_err());
    }

    // FAIL: BAD: 曖昧なテスト名
    #[test]
    fn test_works() { }

    #[test]
    fn it_does_stuff() { }
}
```

---

## コードの臭い検出

### 1. 長い関数（50行超）
```rust
// FAIL: 50行を超えていたら分割を検討
fn handle_everything() { /* 100行 */ }

// PASS: 小さな関数に分解
fn handle_input(input: Input) -> Command { validate_and_parse(input) }
fn execute_command(cmd: Command) -> Result<(), Error> { ... }
```

### 2. 深いネスト
```rust
// FAIL: BAD: 5段以上のネスト
if let Some(user) = user {
    if let Some(market) = market {
        if market.is_active {
            if user.has_permission() {
                // 処理
            }
        }
    }
}

// PASS: GOOD: 早期リターン（ガード節）
let Some(user) = user else { return; };
let Some(market) = market else { return; };
if !market.is_active { return; }
if !user.has_permission() { return; }
// 処理
```

### 3. clippy 警告の放置
```bash
# すべての clippy 警告を修正すること
cargo clippy -- -D warnings
```

clippy 警告は設計上の問題を示すことが多い。`#[allow(...)]` で黙らせるのは最終手段。

---

**Remember**: コードの品質は交渉の余地がない。明確で保守しやすいコードが、高速な開発と自信を持ったリファクタリングを可能にする。
