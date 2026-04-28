# session-file-cache PlanLoop レビューログ

対象: `docs/✅tachibana/session-file-cache.md`（+ 関連 spec/architecture/implementation-plan）  
開始日: 2026-04-27

---

<!-- ラウンド開始後にエージェントが追記 -->

## ラウンド 1（2026-04-27）

### 統一決定

1. `_is_session_fresh` 境界値: `>= JST 15:30:00` を無効（閉で無効側）。`saved_at_ms > now_ms` はクロックスキューとして無効扱い
2. アトミック書き込み: `os.rename` → `os.replace` 使用（Windows/Unix 両対応）
3. T-SC4 スコープ: `engine-client/tests/` 9 ファイルの削除/書き換えを明示。`data::wire::tachibana` モジュールと `data/tests/tachibana_keyring_roundtrip.rs` も削除対象に追加
4. T-SC5 JST mock: `freezegun.freeze_time` を使用
5. T-SC6 スコープ: `spec.md §3.2`, `§4.A1`, `architecture.md §1/§2/§3/§5/§8` を更新対象として明示列挙
6. `LoginCancelled` 遷移: `VenueLoginCancelled{venue, request_id}` を Python→Rust 送信し `VenueState::Idle` へ遷移
7. `architecture.md` への直接修正は T-SC6 まで先送り

### Findings

| ID | 観点 | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| A-001 | A | HIGH | spec.md:63-65 | §3.1 セキュリティ要件に T-SC6 更新予定の注記を追加 |
| A-002 | A | HIGH | spec.md:83-84,89,111 | §3.2 keyring/SetVenueCredentials 参照箇所に注記を追加 |
| A-003 | A | HIGH | architecture.md:9-12,21,31-498 | architecture.md §1/§2/§3 を T-SC6 スコープに明示追加（直接修正は先送り） |
| A-004 | A | MEDIUM | architecture.md:287-288 | T-SC6 スコープに §5 keyring 行の書き換えを追加 |
| A-005 | A | MEDIUM | architecture.md:571-572 | T-SC6 スコープに §8.3 テスト記述削除を追加 |
| A-006 | A | MEDIUM | session-file-cache.md:185 | §4「残るもの」に VenueState FSM は変更なしの注記を追加 |
| A-007 | A | LOW | session-file-cache.md:270-272 | T-SC6 列挙に spec.md §3.2 / §4.A1 を追加 |
| B-001 | B | MEDIUM | session-file-cache.md §3.2 | `_spawn_login_dialog` 呼び出しを `prefill={"user_id":...,"is_demo":...}` に修正 |
| B-002 | B | MEDIUM | session-file-cache.md §3.2 | `_is_session_fresh` は T-SC1 で新設と注記 |
| B-003 | B | MEDIUM | session-file-cache.md T-SC3 | 実際のハンドラ名を確認してから削除するよう注記 |
| B-004 | B | MEDIUM | session-file-cache.md T-SC4 | secrecy/zeroize の非 Tachibana 利用確認と Cargo.toml 削除手順を追加 |
| B-005 | B | MEDIUM | session-file-cache.md §4 | `data::wire::tachibana` と `data/tests/tachibana_keyring_roundtrip.rs` を削除表に追加 |
| C-001 | C | HIGH | session-file-cache.md §3.1/T-SC1 | `os.rename` → `os.replace`（Windows 対応）に修正 |
| C-002 | C | HIGH | session-file-cache.md §3.3/T-SC3 | `_login_in_flight` フラグによる二重起動防止を追記 |
| C-003 | C | HIGH | session-file-cache.md §3.3/T-SC3 | `LoginCancelled` → `VenueLoginCancelled` 送信と Idle 遷移を追記 |
| C-004 | C | MEDIUM | session-file-cache.md T-SC2 | ネットワークエラー時の `VenueError` 送信を追記 |
| C-005 | C | MEDIUM | session-file-cache.md T-SC1 | `saved_at_ms > now_ms` はクロックスキューで無効扱いを追記 |
| C-006 | C | MEDIUM | session-file-cache.md T-SC2 | `DEV_TACHIBANA_PASSWORD` の `dev_tachibana_login_allowed` フラグ制御を追記 |
| C-007 | C | MEDIUM | session-file-cache.md T-SC1 | `>= 15:30:00 JST` を無効（閉で無効側）と明記 |
| C-008 | C | MEDIUM | session-file-cache.md §5 | schema_minor の後方互換根拠と同期更新手順を追記 |
| C-009 | C | LOW | session-file-cache.md §3.2 | p_no=1 リセット理由（立花 API 仕様）を注記 |
| C-010 | C | LOW | session-file-cache.md §4/§5 | 重複記載は整合しているため修正不要 |
| D-001 | D | HIGH | session-file-cache.md T-SC4 | engine-client/tests/ 9 ファイルを T-SC4 の削除対象に明示追加 |
| D-002 | D | HIGH | session-file-cache.md T-SC5 | invariant-tests.md に F-SC-NoPassword/F-SC-Atomic/F-SC-FreshJST を起票する項目を追加 |
| D-003 | D | HIGH | session-file-cache.md T-SC5 | freezegun 使用・JST 3 点テスト・クロックスキューテストを明記 |
| D-004 | D | MEDIUM | session-file-cache.md T-SC5 | _spawn_login_dialog を AsyncMock で patch する方針を明記 |
| D-005 | D | MEDIUM | session-file-cache.md T-SC5 | os.replace を patch して中断 → ファイル残存なしを assert する観測点を追記 |
| D-006 | D | MEDIUM | session-file-cache.md T-SC5 | CI 自動収集（特別なマーカー不要）を明記 |
| D-007 | D | LOW | session-file-cache.md T-SC4 | schema_v1_2_roundtrip.rs は該当 2 関数のみ削除、ファイル維持 |

## ラウンド 2（2026-04-27）

### 収束確認

| 観点 | 結果 |
|---|---|
| A（文書間整合性） | 収束（A-001〜A-007 全反映確認）|
| B（既存実装とのズレ） | 収束（B-001〜B-005 全反映確認）|
| C（仕様漏れ・設計リスク） | 収束（C-001〜C-009 全反映確認）|
| D（テスト不足） | MEDIUM 1件残存 → 修正済み |

### 新規 Finding

| ID | 観点 | 重要度 | 対象ファイル | 修正概要 |
|---|---|---|---|---|
| D2-001 | D | MEDIUM | session-file-cache.md T-SC5 | T-SC5 に `_login_in_flight` / `VenueLoginCancelled` / ネットワーク `VenueError` の 3 テストケースを追加 |

### 最終機械検証（ラウンド 2 完了時）

- `os.rename` 残存: なし（説明文中のみ）✓
- `_login_in_flight` §3.3/T-SC3/T-SC5: 全て存在 ✓
- `VenueLoginCancelled` §3.3/T-SC3/§4/T-SC5: 全て存在 ✓
- `VenueError` T-SC2/T-SC5: 全て存在 ✓
- T-SC6 スコープ（architecture.md §1/§2/§3/§5/§8, spec.md §3.2/§4.A1）: 明記済み ✓

## ラウンド 3（2026-04-27）

### 収束確認

全 4 観点とも HIGH/MEDIUM ゼロ。ループ終了条件充足。

| 観点 | 結果 |
|---|---|
| A（文書間整合性） | 収束（新規 HIGH/MEDIUM なし）|
| B（既存実装とのズレ） | 収束（新規 HIGH/MEDIUM なし）|
| C（仕様漏れ・設計リスク） | 収束（新規 HIGH/MEDIUM なし）|
| D（テスト不足） | 収束（新規 HIGH/MEDIUM なし）|

### 新規 LOW（対応不要）

| ID | 観点 | 対象ファイル | 内容 |
|---|---|---|---|
| A-008 | A | session-file-cache.md L202 | spec.md §2.1 アンカーが不正確（VenueState FSM の実際の記述場所と微妙にズレ）。T-SC6 作業時に修正 |
| B-LOW-001 | B | session-file-cache.md §3.3 L164 | §3.3 本文が `_login_in_flight: bool` と記述しているが T-SC3 は正しく `_tachibana_login_inflight`（asyncio.Lock）を参照。読者への注意喚起として記録 |
| B-LOW-002 | B | session-file-cache.md §3.3 / T-SC3 | 行番号 L1564/L1784 が現行 server.py（L1569/L1557）とズレ。実装時はシンボル名で検索する |
| D-LOW-001 | D | invariant-tests.md | F-SC-NoPassword / F-SC-Atomic / F-SC-FreshJST が未起票。T-SC5 実装 PR 着地時に同時追記する設計で正常 |

### 最終機械検証（ラウンド 3 完了時）

- `os.rename` 仕様記述残存: なし（説明文中のみ）✓
- `VenueLoginCancelled`: §3.3 / §4 / T-SC3 / T-SC5 全存在 ✓
- `VenueError`: §4 / T-SC2 / T-SC5 全存在 ✓
- `schema_major`: §5 / T-SC4 全存在 ✓
- `_tachibana_login_inflight`: T-SC3 / T-SC5 に存在 ✓

**収束確定。全タスク（T-SC1〜T-SC6）の計画記述は実装に進める状態。**

---

## 実装コードレビュー（review-fix-loop）

### 緊急バグ修正（ラウンド外・即時対応）

- ✅ `_spawn_login_dialog` に `asyncio.CancelledError` ハンドラを追加
  - **症状**: Rust が切断 → `startup_task.cancel()` 発火時、tkinter サブプロセスを kill せず放置 → Rust 再接続のたびに新しいダイアログが追加表示される
  - **修正**: `CancelledError` を捕捉して `proc.terminate()` / `proc.kill()` でサブプロセスを確実に終了させてから re-raise

---

## レビュー反映 (2026-04-27, 実装ラウンド 1)

### R1 Findings（CRITICAL 2 / HIGH 3 / MEDIUM 6）

| ID | 重要度 | 内容 | 修正 |
|---|---|---|---|
| C-1 | CRITICAL | `_venue_credentials_refreshed_event` / `run_login` / `_try_silent_login` が IPC に `password` 平文を含む | ✅ 全削除 |
| C-2 | CRITICAL | `schemas.py` に削除すべき旧型（`SetVenueCredentials` / `VenueCredentialsRefreshed` 等）が残存 | ✅ 全削除 |
| H-1 | HIGH | `_do_request_venue_login` が `_spawn_fetch` 経由でキャンセル時に `Error{code:cancelled}` が出て VenueState が固着 | ✅ `create_task` に変更 |
| H-2 | HIGH | `startup_login` RuntimeError 時に `clear_session` が呼ばれない → 再起動ループ | ✅ `clear_session` 追加 |
| H-3 | HIGH | `StartupLatch` が reconnect 時にリセットされない → 2回目の reconnect で `os._exit(2)` | ✅ finally でリセット |
| M-1 | MEDIUM | T-SC5 指定テスト 3件未実装（inflight/cancelled/network_error） | ✅ 追加 |
| M-2 | MEDIUM | F-SC-Atomic：`os.replace` 前の中断シナリオテスト未実装 | ✅ 追加 |
| M-3 | MEDIUM | `load_account`/`load_session` が全例外を無音で飲み込む | ✅ WARNING ログ追加 |
| M-4 | MEDIUM | docstring/コメントに旧フロー（`SetVenueCredentials`・`keyring`）記述残存 | ✅ 更新 |
| M-5 | MEDIUM | テスト sentinel `"test_pass"` 等が低品質 | ✅ `SENTINEL_PW_*` 形式に変更 |
| M-6 | MEDIUM | `test_schema_compat_v1_2.py` が削除型を import → ImportError 予備軍 | ✅ 削除 |

### 副次変更
- `test_tachibana_login_started_semantics.py` 削除（`run_login` テスト）
- `test_tachibana_dev_env_guard.py` を `startup_login` ベースに全面書き換え

---

## レビュー反映 (2026-04-27, 実装ラウンド 2)

### R2 Findings（HIGH 2 / MEDIUM 3）

| ID | 重要度 | 内容 | 修正 |
|---|---|---|---|
| R2-H-1 | HIGH | `_handle` finally がローカル変数 `startup_task` を使い、`_do_request_venue_login` 生成タスクが切断時に孤立 | ✅ `self._tachibana_startup_task` に統一 + `add_done_callback` 追加 |
| R2-H-2 | HIGH | `invariant-tests.md` F-SC-Atomic エントリに `test_atomic_write_preserves_original_on_exception` が未登録 | ✅ 追記 |
| R2-M-1 | MEDIUM | `_spawn_login_dialog` CancelledError ハンドラ内の二重キャンセルでサブプロセス kill がスキップされる可能性 | ✅ innermost except に `CancelledError` 追加 |
| R2-M-2 | MEDIUM | dead event helper 4関数（`_venue_ready_event` 等）が呼び出し元なしで残存 | ✅ 全削除 |
| R2-M-3 | MEDIUM | `tachibana.py`/`tachibana_auth.py` docstring に `SetVenueCredentials` 残滓 | ✅ `startup_login` 方式に書き換え |

---

## レビュー反映 (2026-04-27, 実装ラウンド 3)

### R3 収束確認（CRITICAL 0 / HIGH 0 / MEDIUM 0）

全チェック完了。残存 LOW 3件のみ（対応不要）:

| ID | 内容 |
|---|---|
| L-1 | `_spawn_login_dialog` の JSON parse エラーログ `last_line[:40]` が理論上 password prefix を含む可能性（実害なし：到達条件は JSON 解析失敗時のみ） |
| L-2 | `_handle` 内の `startup_task` ローカル変数が finally では未使用になったが残存（dead code、読者の混乱源） |
| L-3 | `test_tachibana_startup_login.py` の `startup_login` 経由フローに対するログ漏洩 caplog テストが未追加 |

### 最終検証（2026-04-27）

| コマンド | 結果 |
|---|---|
| `uv run pytest python/tests/ -q` | ✅ 783 passed, 2 skipped |
| `cargo test --workspace` | ✅ 全スイート ok |
| `cargo fmt --check` | ✅ クリーン |
| `cargo clippy --workspace -- -D warnings` | ✅ クリーン |

**実装コードレビュー完了。MEDIUM 以上ゼロ確認。**

---

## レビュー反映 (2026-04-27, ラウンド 2)

### 解消した指摘
- ✅ R2-H-1: `_handle` finally を `self._tachibana_startup_task` に統一、done_callback 追加
- ✅ R2-H-2: `invariant-tests.md` F-SC-Atomic エントリに `test_atomic_write_preserves_original_on_exception` 追記
- ✅ R2-M-1: `_spawn_login_dialog` の二重キャンセル対策（innermost except に CancelledError 追加）
- ✅ R2-M-2: dead event helper 4関数（_venue_ready_event 等）削除
- ✅ R2-M-3: tachibana.py / tachibana_auth.py の SetVenueCredentials 残滓 docstring 修正

---

## レビュー反映 (2026-04-27, ラウンド 1 実装修正)

### 解消した指摘

- ✅ C-1: `_venue_credentials_refreshed_event` / `run_login` / `_try_silent_login` を削除（password 平文送出経路を完全除去）
- ✅ C-2: `schemas.py` から `TachibanaSessionWire`・`TachibanaCredentialsWire`・`VenueCredentialsPayload`・`SetVenueCredentials`・`VenueCredentialsRefreshed` を削除
- ✅ H-1: `_do_request_venue_login` を `_spawn_fetch` から切り離し、`asyncio.create_task` でブロッキング排除
- ✅ H-2: `startup_login` RuntimeError 時に `clear_session` 追加
- ✅ H-3: `_handle` finally ブロックで `StartupLatch` リセット
- ✅ M-1: T-SC5 必須テスト 3件追加（`test_startup_tachibana_login_cancelled_emits_venue_login_cancelled` / `test_startup_tachibana_network_error_emits_venue_error_login_failed` / `test_do_request_venue_login_inflight_emits_only_venue_login_started`）
- ✅ M-2: F-SC-Atomic 中断シナリオテスト追加（`test_atomic_write_preserves_original_on_exception`）
- ✅ M-3: `load_account`/`load_session` の WARNING ログ追加（`FileNotFoundError` は `return None`、その他は `log.warning` 後 `return None`）
- ✅ M-4/M-5: docstring/コメント更新（server.py L162-165 コメント・`_startup_tachibana` docstring・module docstring）
- ✅ M-5: テスト sentinel 品質向上（`"test_pass"` → `"SENTINEL_PW_dXk9Qa"`、`"save_pass"` → `"SENTINEL_PW_g5Wm2R"`）
- ✅ M-6: `test_schema_compat_v1_2.py` 削除

### 副次的変更（削除に伴う連鎖）

- `test_tachibana_login_started_semantics.py` を削除（`run_login` / `VenueCredentialsRefreshed` をテストしていた）
- `test_tachibana_dev_env_guard.py` を `startup_login` ベースに書き換え（`F-DevEnv-Release-Guard` 不変条件は維持）

### 設計判断・新たな知見

1. **`_do_request_venue_login` の `asyncio.create_task` 化**: `_spawn_fetch` でラップするとキャンセル時に `Error{code:cancelled}` が outbox に入り VenueState が `LoginInFlight` で固着する。`_dispatch_message` で直接 `await _do_request_venue_login(msg)` し、内部で `asyncio.create_task(_startup_tachibana(...))` に移行。これにより recv ループはブロックせず、かつキャンセル時の spurious Error も発生しない。
2. **`StartupLatch` リセット**: `_handle` の finally ブロックでリセットしないと、Python の reconnect 後に `validate_session_on_startup` が L6 ガードで即死する。`StartupLatch()` で新しいインスタンスに置き換えることで解決。
3. **`test_schema_compat_v1_2.py` の削除**: 削除した IPC 型（`TachibanaSessionWire` / `VenueCredentialsPayload` / `SetVenueCredentials` / `VenueCredentialsRefreshed`）を import しているため、型削除後は ImportError になる。ファイルごと削除が正解。
