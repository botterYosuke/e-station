---
title: コーディング規約
status: draft
authored: 2026-05-08
sources:
  - AGENTS.md
  - Cargo.toml
  - clippy.toml
  - rustfmt.toml
  - rust-toolchain.toml
  - pyproject.toml
---

# コーディング規約

## Rust

### Formatting / Lint

- `cargo fmt` を commit 前に必ず実行（rustfmt 設定: `rustfmt.toml`、max line 100）
- `cargo clippy -- -D warnings`（warnings は error 扱い）
- clippy thresholds: `clippy.toml` で `too-many-arguments-threshold = 16`、
  `enum-variant-name-threshold = 5` を緩和済み
- toolchain: `rust-toolchain.toml` で `1.95.0` にピン

### 命名

- 関数 / 変数 / モジュール: `snake_case`
- 型 / トレイト / enum: `PascalCase`
- 定数 / static: `SCREAMING_SNAKE_CASE`
- ライフタイム: `'a` / `'de`、複雑な場合のみ `'input` 等

### イミュータビリティ

- `let` を既定とし、必要なときだけ `let mut`
- in-place mutation より新しい値を返す
- 引数は `&str` / `&[T]` を優先、所有が必要なときのみ `String` / `Vec<T>`

### エラーハンドリング

- `Result<T, E>` + `?` で伝播。production code で `unwrap()` / `expect()` 禁止
- ライブラリ層は `thiserror` で typed error を定義（`anyhow` は本プロジェクト未導入）
- `unwrap()` / `expect()` はテストと「真に到達不能」な状態のみ

### 設計パターン

- 外部依存はトレイト境界（Repository pattern）+ コンストラクタ注入
- 区別すべき値型は **Newtype**（`UserId(u64)` 等）で誤代入を型で防ぐ
- 状態は **enum で網羅**、business-critical では `_` ワイルドカード禁止
- `unsafe` は `// SAFETY:` コメントで全不変条件を明記

## Python

- Python 3.11+
- 型注釈は必須相当（Pyright が `pyproject.toml` の `[tool.pyright]` で `extraPaths = ["python"]` 監視）
- データクラスは `pydantic v2` で boundary を型化（`python/engine/schemas.py`）
- 例外は具体型を上げる、ベタな `except Exception` を握りつぶす silent failure を禁止
- ログは `logging` モジュール経由。PII / 秘密情報は `pii_scrub.py` で除去

## 共通

### Silent failure 禁止

handshake timeout、接続断時の state リセット、エラー broadcast の `request_id` フィルタ、
sticky error など、**失敗時に何が起きるかをコードと E2E テストの両方で示す** こと。

### Secrets

- ハードコード禁止。環境変数 + 起動時 fail-fast 検証
- `.env` は `.gitignore`
- Rust 側は `secrecy` / `zeroize` crate で in-memory 保護（既に workspace dependency）

### Commit / PR

- 1 PR = 1 機能 / 1 修正。ドキュメント再編 PR は「リネームのみ」のコミットを冒頭に置き
  git rename 検知を最大化する
- 不具合修正後は必ず `/bug-postmortem` を実行（[development/troubleshooting.md](../development/troubleshooting.md)）
