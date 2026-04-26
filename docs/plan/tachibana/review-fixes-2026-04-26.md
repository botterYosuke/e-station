# implementation-plan-T3.5.md PlanLoop 修正ログ — 2026-04-26

対象: `docs/plan/tachibana/implementation-plan-T3.5.md`
スキル: `.claude/skills/review/SKILL.md` + `.claude/skills/review-fix-loop/PlanLoop.md`
着手: 2026-04-26
ブランチ: `tachibana/phase-1/T3-credential-r6-fixes`

> 本ログは PlanLoop ラウンド単位の Finding と修正概要を記録する。実装ではなく **計画書の整合修正のみ** が対象。

---

## ラウンド 1（2026-04-26）

### 統一決定 1〜18 の反映概要

| # | 統一決定 | 反映概要 |
|---|----------|----------|
| 1 | U1/U3/U4 場所表記 | `src/screen/dashboard/tickers_table.rs::exchange_filter_btn` の venue 行を正本化、spec.md §3.1 の `sidebar.rs` 言及陳腐化を脚注に追記、§7 着手前読み込みリストを書換 |
| 2 | 行番号参照を全廃 | `main.rs` 行番号 → `update()` 関数本体内 grep 再特定、`process.rs:247` → `EngineCommand::Bundled(p).program()` シンボル名、親 plan 行番号参照 → 「親 plan ラウンド 6 繰越 §"繰越 / 次イテレーション" H5/H6/H7/H8/H9」シンボル参照、§7 line 242 を grep 再特定に書換、Step A 作業 3/5 の混線解消 |
| 3 | 状態 enum 1 本化 | `tachibana_ready: bool` + `tachibana_login_in_flight: bool` を廃止、`VenueState{Idle,LoginInFlight,Ready,Error{class,message}}` に統一、`tachibana_banner` フィールド廃止し view 側で `VenueState` から render、§3.2 に明記、`Hello` 再受信で Idle にリセット |
| 4 | 構造ガード AST ベース化 | `syn::parse_file` + `syn::visit::Visit` で `fn update` の `Block` のみ走査。テスト関数名 `update_body_has_no_engine_connection_read` / `update_body_has_no_block_on` / `engine_status_subscription_is_singleton` 確定。`tools/iced_purity_grep.sh` を新設し `.github/workflows/rust.yml::iced-purity-lint` ジョブで CI 化、§3.3 に明記、H7/H8/H9 不変条件追加サブタスク Step A に登録 |
| 5 | 不変条件 ID 登録 | §3.1 に H5〜H9 / U1〜U5 の ID と pin テストの一覧表を新設、各 Step 末尾に `invariant-tests.md` 行追加サブタスク追加 |
| 6 | Step F cancel 注入経路 | stdin "cancel コマンド" を削除し、stdin close (EOF) → helper が `{"status":"cancelled"}` 出力する経路に書換、`review-fixes-2026-04-25.md` ラウンド 4 Group E への参照リンクを併記 |
| 7 | U2 バナー文言の正本 | Rust 側は `severity → palette role` の色マッピングのみ保持、ボタンラベル文言は `VenueError.message` から取得（Phase 1 暫定: `message` 改行区切り）。`action_label` 追加は別 PR §2.3 へ繰越 |
| 8 | `src/notify/` 表記 | 「`src/notify/`」を「`src/notify.rs` および `src/widget/toast.rs`」に修正、新規 `src/widget/venue_banner.rs` 候補は維持 |
| 9 | U3 と LOW-3 境界 | U3 行に「LOW-3 ユーザー明示の再ログイン側に分類」を追記、`Trigger::{Auto,Manual}` enum 導入、`Auto` は `VenueState::Idle && first_open` のみ許可、pin テスト名 `auto_request_login_on_first_open_classified_as_manual_trigger` 確定 |
| 10 | §5 受け入れ基準コマンド原文 + CI ジョブ名 | `cargo check`/`clippy`/`fmt --check`/`test --workspace`/`pytest`/`smoke.sh`/`tachibana_relogin_after_cancel.sh` 全て原文併記、各行末に `.github/workflows/rust.yml::ci-test` 等の CI ジョブ名併記、keyring 5 連続緑追加、E2E nightly + `e2e` ラベル + `OBSERVE_S=60` 明記 |
| 11 | 手動 smoke ネガ系追加 | §5-10 に (a)ポジ + (b)cancel→バナー + (c)Relogin ボタン表示 + (d)二重押下無反応 の 4 項目展開 |
| 12 | secret 焼付きガード | Step A REFACTOR に `engine_connection_debug_does_not_leak_credentials` pin テスト追加 |
| 13 | §7 着手前チェックリスト整備 | sidebar venue 行 → `tickers_table.rs::exchange_filter_btn`、`src/notify/` → `src/notify.rs` + `src/widget/toast.rs`、line 番号 → grep 再特定 に書換 |
| 14 | U×Step マトリクス | §3.0 に U1=D / U2=E / U3=C+D / U4=C / U5=F / H5=B / H6=B / H7=A / H8=A / H9=A の対応表追加 |
| 15 | 親 plan 繰越参照 | §2.1 / §2.2 各 ID 行末に「親 plan ラウンド 6 繰越 §"繰越 / 次イテレーション"」参照を付記、§1 親計画の項にも明記 |
| 16 | Step E テーブル駆動テスト | 5 行の入力イベント列 → 期待 `VenueState` 固定テーブルを Step E 本文に明記 |
| 17 | Step C 受け入れ条件確定 | テスト関数名 `metadata_fetch_blocked_until_venue_ready` / `pending_fetch_replays_on_venue_ready`、観測点 `MockFetchMetadata::expect_call().times(0)` → `times(1)` を明記 |
| 18 | Step F ログ grep 正規表現確定 | `EXPECT_STARTED_RE='^.*VenueLoginStarted\{venue:"tachibana"\}.*$'` 定数化、`OBSERVE_S=30` の根拠（handshake 15s + cancel 往復 10s）コメント明記 |

### Finding ID → 修正概要マッピング

| Finding ID | 統一決定 # | 修正概要 |
|------------|------------|----------|
| HIGH-A1 | 2, 4 | `update()` 行番号参照を grep 再特定 + AST ベース構造ガードに置換 |
| HIGH-A2 | 4, 12 | `iced_purity_grep.sh` + AST ガード + secret redaction pin |
| H-C1 | 3, 17 | `VenueState` enum 1 本化、Step C テスト名・観測点確定 |
| H-C2 | 3, 17 | pending fetch replay の pin テスト追加 |
| H-C3 | 3 | `Hello` 再受信時 `VenueState::Idle` リセット明記 |
| H-C4 | 17 | mock 観測点 `times(0)` → `times(1)` を Step C に確定 |
| D-H1 | 3, 9 | `Trigger::{Auto,Manual}` 導入、`LoginInFlight` で重複押下抑止 |
| D-H2 | 9 | U3 = LOW-3 「ユーザー明示」分類を §2.1 に追記 |
| D-H3 | 3 | 二重 bool フラグ廃止、`VenueState` 1 本化 |
| D-H4 | 3, 5 | U1 / U3 の pin テスト名と invariant ID を §3.1 で固定 |
| MED-A1 | 4 | AST ベース化で「単純 `include_str!` grep」から脱却 |
| MED-A2 | 5 | `invariant-tests.md` 登録サブタスクを各 Step 末尾に追加 |
| MED-A3 | 12 | `EngineConnection: Debug` redaction pin を Step A REFACTOR に追加 |
| M-C1 | 17 | Step C テスト関数名・期待呼び出し回数を本文に明記 |
| M-C2 | 3 | pending fetch を `VenueState` 1 本で扱う |
| M-C3 | 17 | `pending_fetch_replays_on_venue_ready` pin 追加 |
| M-C4 | 14 | §3.0 マトリクスで U4=Step C を明示 |
| L-C1 | 14 | §3.0 マトリクスに U×Step 対応表追加 |
| L-C2 | 5 | invariant-tests.md 一覧化 |
| B-H1 | 6 | Step F の cancel 注入経路を stdin EOF に統一 |
| B-M1 | 18 | `EXPECT_STARTED_RE` 定数化と `OBSERVE_S=30` 根拠コメント |
| B-M2 | 10 | E2E shell の CI 組込（nightly + `e2e` ラベル）明記 |
| D-M1 | 7 | バナー文字列リテラルを Rust 側から排除、`message` 改行区切り暫定運用 |
| D-M2 | 7 | `severity → palette role` の色マッピングのみ Rust 側に残す |
| D-M3 | 8 | `src/notify/` → `src/notify.rs` + `src/widget/toast.rs` 修正 |
| D-M4 | 16 | Step E テーブル駆動テスト 5 行を本文明記 |
| D-L1 | 11 | 手動 smoke ネガ系 (b)(c)(d) 追加 |
| D-L2 | 7 | `action_label` 追加は §2.3 別 PR 繰越固定 |
| D-L3 | 1 | spec.md §3.1 line 30 陳腐化注記、別 PR 同期予定 |
| L-A1 | 2 | 親 plan 行番号参照をシンボル参照に置換 |
| L-A2 | 15 | §2.1 / §2.2 各 ID 行末に親 plan 繰越セクション参照追記 |

---

## ラウンド 2（2026-04-26）

### 統一決定 1〜3 の反映概要

| # | 統一決定 | 反映概要 |
|---|----------|----------|
| 1 | 不変条件 ID に `T35-` プレフィックス付与 | §3.1 の 10 件（H5-PathFidelity / H6-KeyringSlotIsolation / H7-NoStaticInUpdate / H8-NoBlockOnInUpdate / H9-SingleRecoveryPath / U1-LoginButton / U2-Banner / U3-AutoRequestLogin / U4-VenueReadyGate / U5-RelogE2E）に `T35-` プレフィックスを付与。§3.1 表頭直後に「`F-H5`/`F-H6` 衝突回避と CI ガード `test_invariant_table_covers_all_ids` の `F-[A-Z0-9-]+` パターンとの関係」を 1 行追記。各 Step 末尾の `invariant-tests.md` 行追加サブタスクの ID 表記も同様に置換（replace_all で本文中の参照も全て置換）。`-PathFidelity` 等 suffix 無しのタスク ID 記号 `H5/H6/H7/H8/H9` はそのまま残置。 |
| 2 | Step A 作業 0a / 0b 追加 | Step A 作業リスト先頭に 0a（workspace root `tests/` ディレクトリ新設、Rust 統合テスト配置先）と 0b（root crate `Cargo.toml [dev-dependencies]` への `syn = { version = "2", features = ["full", "visit"] }` 追加 + AST 走査の入口仕様）を挿入。 |
| 3 | 親 plan 繰越セクションのリテラル統一 | T3.5 内の `「繰越 / 次イテレーション」` を `「繰越 / 次イテレーション (ラウンド 6 追加)」` に全置換（grep 特定可能化）。 |

### Finding ID → 修正概要マッピング

| Finding ID | 統一決定 # | 修正概要 |
|------------|------------|----------|
| M-R2-1 | 1 | 不変条件 ID に `T35-` プレフィックス付与。`F-H5` / `F-H6` との衝突を回避し、CI ガード `test_invariant_table_covers_all_ids` の `F-[A-Z0-9-]+` パターンが拾う既存エントリと別エントリとして登録される旨を §3.1 に明記。 |
| M-R2-2 | 2 | Step A 作業 0a で Rust 統合テスト用 `tests/` ディレクトリ新設、作業 0b で `syn` の `dev-dependencies` 追加と AST 走査入口仕様を明記。 |
| M-R2-3 | 3 | 「繰越 / 次イテレーション」リテラルを「繰越 / 次イテレーション (ラウンド 6 追加)」に統一し grep 特定可能化。 |
| L-R2-1 | — | （確認のみ）`H5/H6/H7/H8/H9` のタスク ID 記号は不変条件 ID と区別して残置で問題ないことを確認。 |
| L-R2-2 | 1 | replace_all により §3.1 表外の本文中参照（Step A/B/C/D/E/F 末尾の `invariant-tests.md` サブタスク行）も同時置換され、表記揺れが残らないことを確認。 |

