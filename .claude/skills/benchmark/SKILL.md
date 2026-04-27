---
name: benchmark
description: Rust/criterion ベンチマーク。PR 前後のパフォーマンス計測、ベースライン保存・比較、criterion ベンチマークの作成を支援する。
origin: ECC (flowsurface 向け Rust 適応版)
---

# Benchmark — Rust パフォーマンス計測・回帰検出

flowsurface は Rust 製デスクトップアプリのため、ブラウザ指標ではなく **criterion クレート** によるマイクロベンチマークを使う。

## When to Use

- PR の前後でパフォーマンスへの影響を計測したいとき
- ティックデータ処理・チャート描画計算など重要な処理のベースラインを取りたいとき
- 「なんか遅くなった気がする」を数値で確認したいとき
- リファクタリング前後での性能比較

## セットアップ

### Cargo.toml（既に追加済み）

```toml
[dev-dependencies]
criterion = { version = "0.5", features = ["html_reports"] }

[[bench]]
name = "flowsurface"
harness = false
```

### ベンチファイルの場所

```text
flowsurface/
└── benches/
    └── flowsurface.rs    # [[bench]] name = "flowsurface" に対応
```

## ベンチマークの書き方

### 基本パターン

```rust
// benches/flowsurface.rs
use criterion::{black_box, criterion_group, criterion_main, Criterion};

fn bench_tick_processing(c: &mut Criterion) {
    let ticks = generate_test_ticks(1000);

    c.bench_function("process 1000 ticks", |b| {
        b.iter(|| process_ticks(black_box(&ticks)))
    });
}

criterion_group!(benches, bench_tick_processing);
criterion_main!(benches);
```

> `black_box()` は必ず使う。コンパイラの最適化でベンチ対象が消えるのを防ぐ。

### 複数入力サイズの比較

```rust
use criterion::{BenchmarkId, Criterion};

fn bench_candle_aggregation(c: &mut Criterion) {
    let mut group = c.benchmark_group("candle_aggregation");

    for size in [100, 1_000, 10_000].iter() {
        let ticks = generate_test_ticks(*size);
        group.bench_with_input(
            BenchmarkId::from_parameter(size),
            &ticks,
            |b, ticks| b.iter(|| aggregate_candles(black_box(ticks))),
        );
    }
    group.finish();
}
```

### 非同期処理のベンチ

```rust
use criterion::async_executor::TokioExecutor;

fn bench_async_fetch(c: &mut Criterion) {
    c.bench_function("fetch orderbook", |b| {
        b.to_async(TokioExecutor).iter(|| async {
            fetch_orderbook(black_box("BTCUSDT")).await
        });
    });
}
```

## 計測コマンド

```bash
# 全ベンチ実行（HTML レポートも生成）
cargo bench

# 特定のベンチのみ実行
cargo bench --bench flowsurface

# フィルタ（名前が一致するベンチのみ）
cargo bench -- tick_processing

# ベースラインを保存して比較
cargo bench -- --save-baseline before_refactor
# ... リファクタリング後 ...
cargo bench -- --baseline before_refactor
```

## Before/After 比較ワークフロー

PR のパフォーマンス影響を数値化する手順：

```bash
# 1. main ブランチでベースラインを取る
git checkout main
cargo bench -- --save-baseline main

# 2. フィーチャーブランチに切り替える
git checkout feature/my-optimization

# 3. 比較実行
cargo bench -- --baseline main
```

出力例：

```
tick_processing/1000  time: [1.2345 ms 1.2456 ms 1.2567 ms]
                      change: [-15.234% -14.891% -14.548%] (p = 0.00 < 0.05)
                      Performance has improved.
```

## ベースラインの保存先

criterion のベースラインは `target/criterion/` に保存される（git 管理外）。
チームで共有する場合は `docs/benchmarks/` に結果を手動でコピーして commit する：

```bash
# HTML レポートを docs に保存
cp -r target/criterion/ docs/benchmarks/$(date +%Y%m%d)/
```

## ベンチマーク対象の特定

flowsurface で計測価値が高い処理：

| 対象 | モジュール | 理由 |
|------|-----------|------|
| ティックデータ処理 | `data/` | 大量データのパース・集計 |
| ローソク足集計 | `data/src/aggr/` | リアルタイム更新のホットパス |
| チャート描画計算 | `src/chart/` | フレームごとの再計算 |
| オーダーブック処理 | `exchange/` | bid/ask の差分更新 |

## Best Practices

**DO:**
- `black_box()` で最適化を防ぐ
- 同じ条件（同じマシン、同じ負荷状態）でベースラインと比較する
- 数回実行して安定した結果を使う
- ベンチ関数には現実的なデータサイズを使う

**DON'T:**
- バッテリー駆動・高負荷状態でベンチを取らない
- 1 回の計測だけで判断しない（criterion が自動で複数回実行する）
- `println!` をベンチ内で使わない（I/O がボトルネックになる）
