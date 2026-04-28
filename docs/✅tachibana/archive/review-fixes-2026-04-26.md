# implementation-plan-T3.5.md PlanLoop 修正ログ — 2026-04-26

対象: `docs/✅tachibana/implementation-plan-T3.5.md`
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

---

## ラウンド 3（2026-04-26）

### 統一決定 1〜5

- **統一決定 1**: `sidebar.rs` 表記を `src/screen/dashboard/tickers_table.rs::exchange_filter_btn` に全置換する（T3.5 Step D 正本配置の確定。spec.md / implementation-plan.md / inventory-T0.md / architecture.md / invariant-tests.md / open-questions.md / README.md / review-fixes 系の計 10 ファイルへ波及）。
- **統一決定 2**: VenueState 用語の使い分けを正本化する。`VenueLoginStarted` / `VenueLoginCancelled` / `VenueLoginReady` / `VenueLoginError` は **Python engine event DTO 名**、Rust UI 状態は `VenueState{Idle, LoginInFlight, Ready, Error{class,message}}` の 1 本化に統一する（spec.md §UI / invariant-tests.md L69 / architecture.md L279 等で書き分け）。
- **統一決定 3**: `[~] (deferred to T3.5)` 完了マークを `[x] (T3.5 Step C-F 着地)` に反映する（implementation-plan.md L481-505 の T7-Phase 2 完了マーク更新）。
- **統一決定 4**: 行番号参照の他ファイル波及置換を完了する（inventory-T0.md L41 の `sidebar.rs:249` 削除、spec.md L11 の `adapter.rs#L264` → `Exchange::TachibanaStock`、implementation-plan.md L248/L352/L617 の `open-questions.md#L25` → `#q21-...` アンカー化、L487 の sidebar.rs 残存撲滅）。
- **統一決定 5**: 不変条件 ID の `T35-` prefix を親 `implementation-plan.md` 本文にも全面採用し、`invariant-tests.md` ヘッダの CI ガード regex を `F-[A-Z0-9-]+` から `(F|T35)-[A-Z0-9-]+` に拡張する（既存 F-* 系統と T35-* 系統を同一 CI ガードで pin）。

### Finding ID → 修正概要マッピング

| Finding ID | 統一決定 # | 観点 | 対象ファイル | 修正概要 |
|------------|------------|------|---------------|----------|
| A-H1 | 1 | A | spec.md §2.1 | `sidebar.rs` 配置記述を `tickers_table::exchange_filter_btn` に書換 |
| A-H2 | 1, 3 | A | implementation-plan.md L288, L487 | `sidebar.rs` 残存記述削除 + `[~] (deferred to T3.5)` → `[x] (T3.5 Step C-F 着地)` |
| A-M1 | 4 | A | inventory-T0.md L41 | `sidebar.rs:249` 行番号参照削除 + Tachibana ログイン UI 所在を `tickers_table::exchange_filter_btn` に補正 |
| A-M2 | 3 | A | implementation-plan.md L481-505 | T7-Phase 2 行 `[~]` → `[x] (T3.5 Step C-F 着地)` 完了マーク更新 |
| A-M3 | 2 | A | spec.md §UI | 旧 enum 並存記述を撤去し `VenueState{Idle/LoginInFlight/Ready/Error}` に 1 本化、Python DTO 名 `VenueLoginStarted/Cancelled/Ready/Error` との書き分けを脚注で明示 |
| A-L1 | 4 | A | implementation-plan.md L248, L617 | `open-questions.md#L25` 行番号アンカー → `#q21-...` 安定アンカーに置換 |
| A-L2 | 5 | A | invariant-tests.md ヘッダ | `T35-` prefix と既存 `F-` の併存方針、CI grep regex `(F|T35)-[A-Z0-9-]+` への拡張関係を冒頭に注記 |
| A-L3 | — | A | README.md | 文書構成テーブルに T3.5 文書群（spec / implementation-plan / review-fixes-2026-04-25 / review-fixes-2026-04-26）4 行を追記 |
| B-H1 | — | B | architecture.md L541 | `tests/integration/tachibana_handshake.rs` 仮称を実シンボル（`tests/engine_status_subscription_is_singleton.rs` 等）に差替 |
| B-H2 | 2 | B | invariant-tests.md L69 | `set_tachibana_ready` 旧 API 呼出 pin → `VenueState::Ready` 遷移 pin に書換 |
| B-H3 | — | B | architecture.md L279 | UI bridge 流路に `Message::TachibanaVenueEvent` 等の Python event DTO → `VenueState` 遷移経路を追加 |
| B-M1 | 4 | B | inventory-T0.md / implementation-plan.md L352 | 行番号参照を `path::symbol` 形式に機械置換 |
| B-M2 | 4 | B | spec.md L11 | `adapter.rs#L264` → `Exchange::TachibanaStock` シンボル参照に置換 |
| B-M3 | 1 | B | implementation-plan.md L486-499 | H3 着地アンカーを `tickers_table::sidebar_login_button_emits_request_venue_login` テスト関数名で明記 |
| B-L1 | — | B | architecture.md L284 | UI bridge 説明に `VenueState` FSM が前提であることの脚注を追加 |
| B-L2 | — | B | README.md L48-52 | T3.5 着地（Step C-F 完了）を反映 |
| B-L3 | — | B | open-questions.md L60 | 実装ファイル path リンク（`src/venue_state.rs` / `src/widget/venue_banner.rs`）を追記 |
| C-H1 | 1 | C | spec.md §2.1 | A-H1 と統合（`sidebar.rs` 表記撲滅） |
| C-H2 | — | C | invariant-tests.md | `T35-U2-BannerRedaction` 不変条件を新設し pin テストを登録 |
| C-H3 | — | C | open-questions.md Q42 | 対象 path に `src/widget/venue_banner.rs` / `src/venue_state.rs` を追加 |
| C-M1 | — | C | spec.md §4 末尾 | U5 E2E が HTTP API 着地までは `exit 77` で skip する旨を明示 |
| C-M2 | — | C | implementation-plan.md T7 / Phase O1 | `replay_api.rs` 新設タスクを追加し U5 skip 解除条件として紐付 |
| C-M3 | — | C | invariant-tests.md | `F-DevEnv-Release-Guard` 不変条件を新設（release ビルドでの dev 既定値漏れ防止） |
| C-M4 | — | C | architecture.md §2.1 | `EngineConnection: Debug` の `finish_non_exhaustive` 規約を明文化（secret 焼付き防止） |
| C-L1 | — | C | invariant-tests.md | `F-H5` の `second_password.is_none()` pin を追加 |
| C-L2 | — | C | README.md L60 | `DEV_TACHIBANA_DEMO` の既定値 `true` を補完記載 |
| C-L3 | 5 | C | invariant-tests.md L11-15 | CI grep regex を `F-[A-Z0-9-]+` → `(F|T35)-[A-Z0-9-]+` に拡張 |
| D-H1 | — | D | invariant-tests.md | 各 pin テスト行に「実行コマンド」列を追加（`cargo test --test ...` / `pytest ...` / `bash tests/e2e/...` を明示） |
| D-H2 | 5 | D | implementation-plan.md T4 | `T35-*` 全 11 件の pin テストが緑であることの listing を T4 受け入れ基準に追加 |
| D-M1 | — | D | invariant-tests.md `T35-H7/H8/H9` | `tools/iced_purity_grep.sh` の assert を補助証跡として併記 |
| D-M2 | — | D | invariant-tests.md `T35-H6/U5` | keyring 5 連続緑 / nightly + `e2e` ラベル運用を追記 |
| D-L1 | — | D | invariant-tests.md `T35-U2-Banner` | 11 関数の glob を展開して個別関数名を明記 |


---

## ラウンド 5（2026-04-26）

> ラウンド 4 はレビューのみ実施で修正なしのため、本セクションをラウンド 5 として記録する。

### 統一決定 1〜3

- **統一決定 1**: R3 で取りこぼした置換を完了する。`implementation-plan.md` L248 / L618 の `open-questions.md#L25` を `#q21--demo-環境の運用時間` 安定アンカーに置換し、`architecture.md` §7.5.1 の不変条件 ID drift（`T35-U2-StatusBanner` → `T35-U2-Banner`、`T35-U3-AutoFire` → `T35-U3-AutoRequestLogin`）を `invariant-tests.md` 正本に揃える。
- **統一決定 2**: T4 受け入れ基準の `T35-*` listing を `invariant-tests.md` 正本（13 件）と同期する。`T35-H7-DebugRedaction` / `T35-U2-BannerRedaction` を追加し、誤名 `T35-VenueState` を `T35-U4-FSM` に正規化する（implementation-plan.md L555）。
- **統一決定 3**: `invariant-tests.md` に「関連 SKILL ID」列を新設し、全 `T35-*` / `F-*` 行に SKILL R1〜R10 を逆引きで紐付ける。これにより review-fixes 系（R1〜R10）と pin テストの双方向トレーサビリティを確保する。

### Finding ID → 修正概要マッピング

| Finding ID | 統一決定 # | 観点 | 対象ファイル | 修正概要 |
|------------|------------|------|---------------|----------|
| H-1 | 1 | A | implementation-plan.md L248, L618 | `open-questions.md#L25` 行番号アンカー → `#q21--demo-環境の運用時間` 安定アンカー化（R3 取りこぼし分） |
| H-2 | 2 | A+D | implementation-plan.md L555（T4 listing） | `T35-*` listing を 11 → 13 件に拡張、ID drift 解消（`T35-VenueState` → `T35-U4-FSM` 正規化） |
| C-H1 | 1 | C | architecture.md §7.5.1 | `T35-U2-StatusBanner` → `T35-U2-Banner` / `T35-U3-AutoFire` → `T35-U3-AutoRequestLogin` の ID drift 解消 |
| M-1 | 3 | A+D | invariant-tests.md L44 `F-DevEnv-Release-Guard` | glob 表記を個別関数名で pin（並列実行時の取りこぼし防止） |
| M-2 | — | A+D | invariant-tests.md L28 `F-H5` | `second_password.is_none()` pin を統合（R3 C-L1 の補強） |
| C-M1 | 3 | C | invariant-tests.md L77 `T35-U2-BannerRedaction` | 関連 SKILL ID 列に R3 / R10 を紐付け、相互参照リンクを追加 |
| C-M2 | 3 | C | invariant-tests.md L44 `F-DevEnv-Release-Guard` | 関連 SKILL ID 列に R10 / R1 を紐付け |
| C-M3 | 3 | C | invariant-tests.md L24-81 | 「関連 SKILL ID」列を新設し、全 `T35-*` / `F-*` 行に R1〜R10 を逆引き紐付け |
| C-L1 | — | C | spec.md §3.2 / architecture.md §7.5.1 | `DismissTachibanaBanner` の FSM 副作用（view-only か `Error → Idle` 遷移か）を実装確認の上で明記 |

---

## ラウンド 7（2026-04-26）

> ラウンド 6 はサニティチェックのみで修正なし（MEDIUM 検出）のため、本セクションをラウンド 7 として記録する。R7 は R6 で検出された MEDIUM 指摘の修正を行う。

### 統一決定 1〜2

- **統一決定 1**: R5 で新設した `invariant-tests.md` の「関連 SKILL ID」列の semantic を厳格化する。列値は `SKILL.md` の R 番号定義に意味的に整合するもののみ記載し、無関係な行は `—` とする。不変条件 ID（`F-*` / `T35-*`）と SKILL R 番号の混在は禁止する（SKILL ID 列に `F-Banner1` 等の不変条件 ID を入れない）。
- **統一決定 2**: ID prefix 規約節を `SKILL R*` 一本化する。R5 で重複列挙していた `R[0-9]+` 単独 prefix の言及を整理し、SKILL 由来の参照は常に `SKILL R*` 形式で表記する。

### Finding ID → 修正概要マッピング

| Finding ID | 統一決定 # | 観点 | 対象ファイル | 修正概要 |
|------------|------------|------|---------------|----------|
| R6-M1 | 1 | サニティ | invariant-tests.md L82 (`T35-U2-Banner`) | 「関連 SKILL ID」列値から不変条件 ID `F-Banner1` を削除（不変条件 ID と SKILL R 番号の混在排除） |
| R6-M2 | 1 | サニティ | invariant-tests.md L69 ほか全 `T35-*` 行 | SKILL R 番号と意味整合しない紐付けを `—` に整理（`T35-H5/H7/H8/H9/U1/U3/U4/U4-FSM` 等は `—`、Credential 漏洩 / 仮想 URL 系のみ R3 / R10 を残す） |
| R6-L2 | 2 | サニティ | invariant-tests.md L8 ID prefix 規約 | `SKILL R*` 一本化、`R[0-9]+` 単独 prefix 言及を整理 |

